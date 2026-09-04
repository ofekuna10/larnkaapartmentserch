"""Publishing & Execution Agent — and the human-in-the-loop parking bay.

Two nodes live here because they are two halves of the same decision:

* :func:`publisher_node` validates the OAuth credential (refreshing it when it
  has expired), then dispatches each approved draft through its platform
  connector. Writes are never retried — a duplicate post on a client's account
  is worse than a failed one — so a failure is recorded and the run continues.
* :func:`human_approval_node` is the terminal state for anything that must not
  auto-publish: it turns drafts into :class:`ApprovalRequest` rows for the
  review UI and leaves the run in ``AWAITING_APPROVAL``.
"""

from __future__ import annotations

import logging
from typing import Any

from app.agents.nodes._common import agent_node
from app.agents.state import (
    AgentState,
    NodeName,
    current_validations,
    latest_validation_for,
)
from app.models.schemas import (
    ApprovalRequest,
    ContentDraft,
    ExecutionStatus,
    PipelineError,
    Platform,
    PublishResult,
    PublishStatus,
    Severity,
)
from app.services.base import OAuthToken, SocialAPIError, TokenExpiredError
from app.services.registry import connector_for, get_token_store
from app.services.sandbox import sandbox_token

log = logging.getLogger(__name__)


async def resolve_token(user_id: str, platform: Platform) -> OAuthToken:
    """Fetch, refresh and persist the credential for one platform.

    Refresh happens here rather than inside the connector so the refreshed
    token is written back exactly once, whatever the platform.
    """
    store = get_token_store()
    token = await store.get(user_id, platform)
    if token is None:
        return sandbox_token(platform, user_id)
    if token.is_expired:
        log.info("publisher: refreshing expired %s token", platform.value)
        connector = connector_for(platform)
        token = await connector.refresh_token(token)
        await store.put(user_id, token)
    return token


@agent_node(NodeName.PUBLISHER, ExecutionStatus.PUBLISHING)
async def publisher_node(state: AgentState) -> dict[str, Any]:
    user_id = state["user_id"]
    drafts: list[ContentDraft] = list(state.get("generated_content", []))
    results: list[PublishResult] = []
    errors: list[PipelineError] = []

    for draft in drafts:
        verdict = latest_validation_for(state, draft.draft_id)
        if verdict is None or not verdict.is_valid:
            results.append(
                PublishResult(
                    draft_id=draft.draft_id,
                    platform=draft.platform,
                    status=PublishStatus.SKIPPED,
                    error="draft did not pass validation",
                )
            )
            continue

        try:
            token = await resolve_token(user_id, draft.platform)
            connector = connector_for(draft.platform)
            results.append(await connector.publish(token, draft))
        except TokenExpiredError as exc:
            errors.append(
                PipelineError(
                    node=NodeName.PUBLISHER, message=str(exc), recoverable=False
                )
            )
            results.append(
                PublishResult(
                    draft_id=draft.draft_id,
                    platform=draft.platform,
                    status=PublishStatus.FAILED,
                    error=f"reconnect required: {exc}",
                )
            )
        except SocialAPIError as exc:
            errors.append(
                PipelineError(node=NodeName.PUBLISHER, message=str(exc))
            )
            results.append(
                PublishResult(
                    draft_id=draft.draft_id,
                    platform=draft.platform,
                    status=PublishStatus.FAILED,
                    error=str(exc),
                )
            )

    published = sum(
        1
        for result in results
        if result.status in (PublishStatus.PUBLISHED, PublishStatus.SCHEDULED)
    )
    failed = [r for r in results if r.status is PublishStatus.FAILED]
    update: dict[str, Any] = {
        "publish_results": results,
        "execution_status": (
            ExecutionStatus.COMPLETED if published or not results
            else ExecutionStatus.FAILED
        ),
        "next_node": None,
        "_detail": f"{published} dispatched, {len(failed)} failed",
    }
    if errors:
        update["errors"] = errors
    return update


@agent_node(NodeName.HUMAN_APPROVAL, ExecutionStatus.AWAITING_APPROVAL)
async def human_approval_node(state: AgentState) -> dict[str, Any]:
    """Park every remaining draft for review, with the reason attached."""
    drafts = {draft.draft_id: draft for draft in state.get("generated_content", [])}
    retries_exhausted = state.get("retry_count", 0) >= state.get("max_retries", 2)
    requests: list[ApprovalRequest] = []

    for verdict in current_validations(state):
        draft = drafts.get(verdict.draft_id)
        if draft is None:
            continue
        requests.append(
            ApprovalRequest(
                draft_id=draft.draft_id,
                platform=draft.platform,
                reason=_approval_reason(verdict, retries_exhausted, state),
                validation=verdict,
            )
        )

    queued = [
        PublishResult(
            draft_id=request.draft_id,
            platform=request.platform,
            status=PublishStatus.QUEUED_FOR_APPROVAL,
            scheduled_for=drafts[request.draft_id].scheduled_for,
        )
        for request in requests
    ]
    return {
        "approval_requests": requests,
        "publish_results": queued,
        "next_node": None,
        "_detail": f"{len(requests)} draft(s) awaiting human approval",
    }


def _approval_reason(
    verdict: Any, retries_exhausted: bool, state: AgentState
) -> str:
    if not verdict.is_valid:
        blockers = [issue.code for issue in verdict.issues if issue.severity is Severity.BLOCKER]
        if retries_exhausted:
            return (
                "Validation failed after "
                f"{state.get('retry_count', 0)} revision(s); unresolved: "
                + ", ".join(blockers or ["unspecified"])
            )
        return "Validation failed: " + ", ".join(blockers or ["unspecified"])
    if verdict.requires_human_approval:
        return "Passed validation but flagged for review."
    if not state.get("auto_publish", False):
        return "Auto-publishing is disabled for this account."
    return "Queued for review."
