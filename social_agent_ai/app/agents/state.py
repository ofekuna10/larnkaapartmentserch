"""The state object every LangGraph node reads from and writes to.

``AgentState`` is a ``TypedDict`` because that is what ``StateGraph`` consumes
natively: a node returns a *partial* dict and LangGraph merges it in, applying
a reducer where one is declared. The values inside are Pydantic v2 models from
``app.models.schemas`` — LangGraph's serialiser round-trips those, so the state
stays checkpointable while nodes still get typed objects instead of raw dicts.

Merge semantics:

* ``node_trace`` / ``errors``  -> appended (every visit and failure is kept).
* everything else             -> last write wins, which is what the retry loop
  wants: ``content_creator_node`` replaces the drafts it revised.
"""

from __future__ import annotations

import operator
from datetime import datetime
from typing import Annotated, Any, Optional, TypedDict

from pydantic import BaseModel, ConfigDict, Field

from app.models.schemas import (
    AnalyticsSummary,
    ApprovalRequest,
    BrandGuideline,
    ContentDraft,
    CritiqueReport,
    ExecutionStatus,
    PipelineError,
    Platform,
    PlatformConnection,
    PostMetrics,
    PublishResult,
    StrategyPlan,
    UserGoals,
    ValidationResult,
    new_id,
    utcnow,
)


class NodeName(str):
    """String constants for node ids, kept in one place to avoid typos."""

    ORCHESTRATOR = "orchestrator_node"
    ANALYTICS = "analytics_node"
    STRATEGY = "strategy_node"
    CONTENT_CREATOR = "content_creator_node"
    VALIDATION = "validation_node"
    PUBLISHER = "publisher_node"
    HUMAN_APPROVAL = "human_approval_stage"


class NodeVisit(BaseModel):
    """One entry in the execution trace; drives observability and billing."""

    model_config = ConfigDict(extra="forbid")

    node: str
    entered_at: datetime = Field(default_factory=utcnow)
    duration_ms: Optional[float] = None
    status: str = "ok"
    detail: str = ""


class AgentState(TypedDict, total=False):
    """Shared pipeline state.

    ``total=False`` so a node can return only the keys it actually changed.
    """

    # --- Identity & inputs --------------------------------------------------
    run_id: str
    user_id: str
    connected_platforms: list[PlatformConnection]
    goals: UserGoals

    # --- Analytics Agent ----------------------------------------------------
    raw_metrics: list[PostMetrics]
    raw_analytics: Optional[AnalyticsSummary]

    # --- Strategy Agent -----------------------------------------------------
    strategy_plan: Optional[StrategyPlan]

    # --- Content Creation Agent --------------------------------------------
    brand_guidelines: list[BrandGuideline]
    generated_content: list[ContentDraft]

    # --- Validation Agent ---------------------------------------------------
    validation_results: list[ValidationResult]
    critiques: list[CritiqueReport]
    retry_count: int
    max_retries: int

    # --- Publishing Agent ---------------------------------------------------
    publish_results: list[PublishResult]
    approval_requests: list[ApprovalRequest]
    auto_publish: bool

    # --- Control plane ------------------------------------------------------
    execution_status: ExecutionStatus
    current_node: str
    next_node: Optional[str]
    node_trace: Annotated[list[NodeVisit], operator.add]
    errors: Annotated[list[PipelineError], operator.add]
    started_at: datetime
    updated_at: datetime
    metadata: dict[str, Any]


def initial_state(
    *,
    user_id: str,
    connected_platforms: list[PlatformConnection],
    goals: Optional[UserGoals] = None,
    max_retries: int = 2,
    auto_publish: bool = False,
    run_id: Optional[str] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> AgentState:
    """Build a fully-populated starting state.

    Every key is seeded so nodes can use ``state["x"]`` without ``.get``
    defensiveness, and so a checkpointed run has a stable shape from step 0.
    """
    now = utcnow()
    return AgentState(
        run_id=run_id or new_id("run"),
        user_id=user_id,
        connected_platforms=list(connected_platforms),
        goals=goals or UserGoals(),
        raw_metrics=[],
        raw_analytics=None,
        strategy_plan=None,
        brand_guidelines=[],
        generated_content=[],
        validation_results=[],
        critiques=[],
        retry_count=0,
        max_retries=max_retries,
        publish_results=[],
        approval_requests=[],
        auto_publish=auto_publish,
        execution_status=ExecutionStatus.PENDING,
        current_node="",
        next_node=None,
        node_trace=[],
        errors=[],
        started_at=now,
        updated_at=now,
        metadata=metadata or {},
    )


# ---------------------------------------------------------------------------
# Read helpers — keep routing logic and nodes free of dict spelunking.
# ---------------------------------------------------------------------------
def active_platforms(state: AgentState) -> list[Platform]:
    """Platforms the run may touch: connected, live, and in scope of the goals."""
    requested = set(state.get("goals", UserGoals()).target_platforms)
    live = [
        connection.platform
        for connection in state.get("connected_platforms", [])
        if connection.is_active and not connection.is_expired
    ]
    if not requested:
        return live
    ordered = [platform for platform in live if platform in requested]
    return ordered


def drafts_by_id(state: AgentState) -> dict[str, ContentDraft]:
    return {draft.draft_id: draft for draft in state.get("generated_content", [])}


def latest_validation_for(state: AgentState, draft_id: str) -> Optional[ValidationResult]:
    """Most recent verdict for one draft (results are appended per attempt)."""
    for result in reversed(state.get("validation_results", [])):
        if result.draft_id == draft_id:
            return result
    return None


def current_validations(state: AgentState) -> list[ValidationResult]:
    """One verdict per draft — the latest attempt for each."""
    latest: dict[str, ValidationResult] = {}
    for result in state.get("validation_results", []):
        latest[result.draft_id] = result
    return list(latest.values())


def is_valid(state: AgentState) -> bool:
    """True when every draft has a passing verdict."""
    verdicts = current_validations(state)
    if not verdicts:
        return False
    if len(verdicts) < len(state.get("generated_content", [])):
        return False
    return all(verdict.is_valid for verdict in verdicts)


def failed_drafts(state: AgentState) -> list[ContentDraft]:
    """Drafts whose latest verdict did not pass — the retry work list."""
    by_id = drafts_by_id(state)
    return [
        by_id[verdict.draft_id]
        for verdict in current_validations(state)
        if not verdict.is_valid and verdict.draft_id in by_id
    ]


def retries_left(state: AgentState) -> int:
    return max(0, state.get("max_retries", 2) - state.get("retry_count", 0))


def needs_human_approval(state: AgentState) -> bool:
    """Approval is required by config, by a verdict, or by an exhausted retry."""
    if not state.get("auto_publish", False):
        return True
    if any(verdict.requires_human_approval for verdict in current_validations(state)):
        return True
    return not is_valid(state)


def touch(node: str, status: ExecutionStatus) -> dict[str, Any]:
    """Standard control-plane fields every node stamps on its return value."""
    return {
        "current_node": node,
        "execution_status": status,
        "updated_at": utcnow(),
    }


# ---------------------------------------------------------------------------
# API-facing projection
# ---------------------------------------------------------------------------
class PipelineSnapshot(BaseModel):
    """Serialisable view of a run, returned by the REST layer."""

    model_config = ConfigDict(extra="forbid")

    run_id: str
    user_id: str
    execution_status: ExecutionStatus
    current_node: str = ""
    retry_count: int = 0
    max_retries: int = 2
    platforms: list[Platform] = Field(default_factory=list)
    analytics: Optional[AnalyticsSummary] = None
    strategy_plan: Optional[StrategyPlan] = None
    generated_content: list[ContentDraft] = Field(default_factory=list)
    validation_results: list[ValidationResult] = Field(default_factory=list)
    publish_results: list[PublishResult] = Field(default_factory=list)
    approval_requests: list[ApprovalRequest] = Field(default_factory=list)
    node_trace: list[NodeVisit] = Field(default_factory=list)
    errors: list[PipelineError] = Field(default_factory=list)
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @classmethod
    def from_state(cls, state: AgentState) -> PipelineSnapshot:
        return cls(
            run_id=state.get("run_id", ""),
            user_id=state.get("user_id", ""),
            execution_status=state.get("execution_status", ExecutionStatus.PENDING),
            current_node=state.get("current_node", ""),
            retry_count=state.get("retry_count", 0),
            max_retries=state.get("max_retries", 2),
            platforms=[c.platform for c in state.get("connected_platforms", [])],
            analytics=state.get("raw_analytics"),
            strategy_plan=state.get("strategy_plan"),
            generated_content=list(state.get("generated_content", [])),
            validation_results=list(state.get("validation_results", [])),
            publish_results=list(state.get("publish_results", [])),
            approval_requests=list(state.get("approval_requests", [])),
            node_trace=list(state.get("node_trace", [])),
            errors=list(state.get("errors", [])),
            started_at=state.get("started_at"),
            updated_at=state.get("updated_at"),
        )
