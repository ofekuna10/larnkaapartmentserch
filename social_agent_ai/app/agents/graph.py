"""The LangGraph ``StateGraph`` that wires the six agents together.

    START
      |
      v
    orchestrator_node ------------------------------(abort)---> END
      |
      v
    analytics_node ---------------------------------(no data)--> END
      |
      v
    strategy_node
      |
      v
    content_creator_node <---------------------+
      |                                        |
      v                                        |
    validation_node                            |
      |  is_valid == False and retry_count < N |
      +----------------------------------------+
      |
      +-- valid + auto-publish ------> publisher_node ------> END
      |
      +-- needs a human -------------> human_approval_stage -> END

The one non-linear edge is the validation gate, and it is the whole point of
the design: content that fails goes back to its author with a structured
critique, at most ``max_retries`` times, and anything still unresolved is
parked for a person instead of being published or silently dropped.
"""

from __future__ import annotations

import logging
from typing import Any, Literal, Optional

from langgraph.graph import END, START, StateGraph

from app.agents.nodes.analytics import analytics_node
from app.agents.nodes.content_creator import content_creator_node
from app.agents.nodes.orchestrator import orchestrator_node
from app.agents.nodes.publisher import human_approval_node, publisher_node
from app.agents.nodes.strategy import strategy_node
from app.agents.nodes.validation import validation_node
from app.agents.state import (
    AgentState,
    NodeName,
    PipelineSnapshot,
    initial_state,
    is_valid,
    needs_human_approval,
    retries_left,
)
from app.core.config import get_settings
from app.models.schemas import ExecutionStatus, PlatformConnection, UserGoals

log = logging.getLogger(__name__)

ValidationRoute = Literal[
    "content_creator_node", "publisher_node", "human_approval_stage"
]


# ---------------------------------------------------------------------------
# Conditional edges
# ---------------------------------------------------------------------------
def route_after_orchestrator(state: AgentState) -> Literal["analytics_node", "__end__"]:
    """Stop before doing any work if the run's preconditions failed."""
    if state.get("execution_status") is ExecutionStatus.FAILED:
        return END
    return NodeName.ANALYTICS


def route_after_analytics(state: AgentState) -> Literal["strategy_node", "__end__"]:
    """No usable history means no plan worth making."""
    if state.get("execution_status") is ExecutionStatus.FAILED:
        return END
    return NodeName.STRATEGY


def route_after_validation(state: AgentState) -> ValidationRoute:
    """The retry gate.

    * failed **and** retries remaining -> back to the content creator with the
      critique attached;
    * otherwise -> publish, unless a human has to look at it first (auto-publish
      off, a flagged verdict, or a draft that never passed).
    """
    valid = is_valid(state)
    if not valid and retries_left(state) > 0:
        log.info(
            "validation: retrying (attempt %s of %s)",
            state.get("retry_count", 0) + 1,
            state.get("max_retries", 2),
        )
        return NodeName.CONTENT_CREATOR
    if needs_human_approval(state):
        return NodeName.HUMAN_APPROVAL
    return NodeName.PUBLISHER


# ---------------------------------------------------------------------------
# Graph construction
# ---------------------------------------------------------------------------
def build_graph() -> StateGraph:
    """The uncompiled graph — useful for inspection and for custom compilation."""
    graph: StateGraph = StateGraph(AgentState)

    graph.add_node(NodeName.ORCHESTRATOR, orchestrator_node)
    graph.add_node(NodeName.ANALYTICS, analytics_node)
    graph.add_node(NodeName.STRATEGY, strategy_node)
    graph.add_node(NodeName.CONTENT_CREATOR, content_creator_node)
    graph.add_node(NodeName.VALIDATION, validation_node)
    graph.add_node(NodeName.PUBLISHER, publisher_node)
    graph.add_node(NodeName.HUMAN_APPROVAL, human_approval_node)

    graph.add_edge(START, NodeName.ORCHESTRATOR)
    graph.add_conditional_edges(
        NodeName.ORCHESTRATOR,
        route_after_orchestrator,
        {NodeName.ANALYTICS: NodeName.ANALYTICS, END: END},
    )
    graph.add_conditional_edges(
        NodeName.ANALYTICS,
        route_after_analytics,
        {NodeName.STRATEGY: NodeName.STRATEGY, END: END},
    )
    graph.add_edge(NodeName.STRATEGY, NodeName.CONTENT_CREATOR)
    graph.add_edge(NodeName.CONTENT_CREATOR, NodeName.VALIDATION)
    graph.add_conditional_edges(
        NodeName.VALIDATION,
        route_after_validation,
        {
            NodeName.CONTENT_CREATOR: NodeName.CONTENT_CREATOR,
            NodeName.PUBLISHER: NodeName.PUBLISHER,
            NodeName.HUMAN_APPROVAL: NodeName.HUMAN_APPROVAL,
        },
    )
    graph.add_edge(NodeName.PUBLISHER, END)
    graph.add_edge(NodeName.HUMAN_APPROVAL, END)
    return graph


def compile_pipeline(checkpointer: Optional[Any] = None) -> Any:
    """Compile the graph.

    Pass a LangGraph checkpointer (``AsyncSqliteSaver``, ``AsyncPostgresSaver``,
    ...) to make runs resumable and to let the approval UI reopen a parked run
    by ``thread_id``.
    """
    return build_graph().compile(checkpointer=checkpointer)


_pipeline: Optional[Any] = None


def get_pipeline() -> Any:
    """Process-wide compiled pipeline; compilation is not free, so cache it."""
    global _pipeline
    if _pipeline is None:
        _pipeline = compile_pipeline()
    return _pipeline


def reset_pipeline() -> None:
    global _pipeline
    _pipeline = None


def pipeline_diagram() -> str:
    """Mermaid source for the compiled graph, for docs and debugging."""
    return get_pipeline().get_graph().draw_mermaid()


# ---------------------------------------------------------------------------
# Entry point used by the API layer
# ---------------------------------------------------------------------------
async def run_pipeline(
    *,
    user_id: str,
    connected_platforms: list[PlatformConnection],
    goals: Optional[UserGoals] = None,
    run_id: Optional[str] = None,
    max_retries: Optional[int] = None,
    metadata: Optional[dict[str, Any]] = None,
    config: Optional[dict[str, Any]] = None,
) -> PipelineSnapshot:
    """Execute one full pipeline run and return a serialisable snapshot.

    ``recursion_limit`` is derived from ``max_retries``: each retry adds a
    content-creation and a validation step, so the ceiling has to grow with it
    or LangGraph aborts a legitimate second revision.
    """
    settings = get_settings()
    retries = settings.max_validation_retries if max_retries is None else max_retries
    state = initial_state(
        user_id=user_id,
        connected_platforms=connected_platforms,
        goals=goals,
        max_retries=retries,
        auto_publish=(
            goals.auto_publish
            if goals is not None and goals.auto_publish is not None
            else settings.auto_publish_enabled
        ),
        run_id=run_id,
        metadata=metadata,
    )

    run_config: dict[str, Any] = {
        "recursion_limit": 12 + retries * 2,
        "configurable": {"thread_id": state["run_id"]},
    }
    if config:
        run_config.update(config)

    final_state = await get_pipeline().ainvoke(state, config=run_config)
    return PipelineSnapshot.from_state(final_state)
