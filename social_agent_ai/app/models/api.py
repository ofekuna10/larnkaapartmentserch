"""Request and response bodies for the HTTP API.

Separate from ``schemas.py`` on purpose: the wire contract can stay stable
while the internal domain model moves.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.agents.state import PipelineSnapshot
from app.models.schemas import (
    ApprovalRequest,
    ContentDraft,
    ExecutionStatus,
    Platform,
    PlatformConnection,
    PublishResult,
    UserGoals,
)


class ApiBase(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HealthResponse(ApiBase):
    status: str = "ok"
    app_env: str
    version: str
    llm_provider: str
    vector_backend: str
    checks: dict[str, str] = Field(default_factory=dict)


class PipelineRunRequest(ApiBase):
    """Kick off a pipeline run for the authenticated account."""

    goals: UserGoals = Field(default_factory=UserGoals)
    platforms: list[Platform] = Field(
        default_factory=list,
        description="Restrict the run to these platforms; empty means all connected",
    )
    wait: bool = Field(
        default=False,
        description="Block until the run finishes instead of returning 202",
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineRunAccepted(ApiBase):
    """202 response: the run is executing in the background."""

    run_id: str
    execution_status: ExecutionStatus
    poll_url: str


class RunListItem(ApiBase):
    run_id: str
    execution_status: ExecutionStatus
    current_node: str = ""
    started_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


class DraftListResponse(ApiBase):
    run_id: str
    drafts: list[ContentDraft]


class ApprovalListResponse(ApiBase):
    pending: list[ApprovalRequest]


class ApprovalDecisionRequest(ApiBase):
    approve: bool
    edited_caption: Optional[str] = Field(
        default=None, description="Reviewer's replacement copy, published verbatim"
    )
    note: str = ""


class ApprovalDecisionResponse(ApiBase):
    draft_id: str
    approved: bool
    publish_result: Optional[PublishResult] = None
    note: str = ""


class ConnectionListResponse(ApiBase):
    connections: list[PlatformConnection]


class OAuthStartResponse(ApiBase):
    platform: Platform
    authorization_url: str
    state: str


class OAuthCallbackResponse(ApiBase):
    platform: Platform
    account_id: str
    connected: bool = True


class BrandVoiceUpsertRequest(ApiBase):
    snippets: list[str] = Field(min_length=1)
    seed_defaults: bool = False


class BrandVoiceUpsertResponse(ApiBase):
    doc_ids: list[str]


class ErrorResponse(ApiBase):
    detail: str


__all__ = [
    "ApprovalDecisionRequest",
    "ApprovalDecisionResponse",
    "ApprovalListResponse",
    "BrandVoiceUpsertRequest",
    "BrandVoiceUpsertResponse",
    "ConnectionListResponse",
    "DraftListResponse",
    "ErrorResponse",
    "HealthResponse",
    "OAuthCallbackResponse",
    "OAuthStartResponse",
    "PipelineRunAccepted",
    "PipelineRunRequest",
    "PipelineSnapshot",
    "RunListItem",
]
