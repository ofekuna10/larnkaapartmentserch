"""Human-in-the-loop review: see what is pending, approve it, publish it."""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, status

from app.agents.nodes.publisher import resolve_token
from app.api.deps import CurrentUser
from app.models.api import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalListResponse,
)
from app.models.schemas import ContentDraft, PublishResult, PublishStatus
from app.services.base import SocialAPIError
from app.services.registry import connector_for
from app.services.run_store import get_run_store

log = logging.getLogger(__name__)

router = APIRouter(prefix="/posts", tags=["posts"])


@router.get(
    "/pending",
    response_model=ApprovalListResponse,
    summary="Drafts awaiting human approval",
)
async def list_pending(user_id: CurrentUser) -> ApprovalListResponse:
    pending = []
    for snapshot in await get_run_store().list(user_id, limit=50):
        pending.extend(snapshot.approval_requests)
    return ApprovalListResponse(pending=pending)


@router.post(
    "/{run_id}/drafts/{draft_id}/decision",
    response_model=ApprovalDecisionResponse,
    summary="Approve or reject a draft",
)
async def decide(
    run_id: str,
    draft_id: str,
    body: ApprovalDecisionRequest,
    user_id: CurrentUser,
) -> ApprovalDecisionResponse:
    """Approving publishes immediately through the platform connector.

    A reviewer may replace the copy; ``edited_caption`` is published verbatim,
    which is the point of having a human in the loop.
    """
    store = get_run_store()
    snapshot = await store.get(user_id, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown run {run_id}"
        )
    draft = next(
        (item for item in snapshot.generated_content if item.draft_id == draft_id), None
    )
    if draft is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"run {run_id} has no draft {draft_id}",
        )

    if not body.approve:
        result = PublishResult(
            draft_id=draft_id,
            platform=draft.platform,
            status=PublishStatus.SKIPPED,
            error=body.note or "rejected by reviewer",
        )
        await _record(store, user_id, snapshot, result)
        return ApprovalDecisionResponse(
            draft_id=draft_id, approved=False, publish_result=result, note=body.note
        )

    to_publish = _apply_edit(draft, body.edited_caption)
    try:
        token = await resolve_token(user_id, draft.platform)
        result = await connector_for(draft.platform).publish(token, to_publish)
    except SocialAPIError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)
        ) from exc

    await _record(store, user_id, snapshot, result)
    return ApprovalDecisionResponse(
        draft_id=draft_id, approved=True, publish_result=result, note=body.note
    )


def _apply_edit(draft: ContentDraft, edited_caption: str | None) -> ContentDraft:
    """Put reviewer copy in ``body`` and clear the parts it replaces."""
    if not edited_caption:
        return draft
    return draft.model_copy(
        update={
            "hook": "",
            "body": edited_caption,
            "call_to_action": "",
            "revision": draft.revision + 1,
        }
    )


async def _record(store, user_id: str, snapshot, result: PublishResult) -> None:  # type: ignore[no-untyped-def]
    """Replace the draft's queued row with the real outcome."""
    results = [
        item for item in snapshot.publish_results if item.draft_id != result.draft_id
    ]
    results.append(result)
    remaining = [
        request
        for request in snapshot.approval_requests
        if request.draft_id != result.draft_id
    ]
    await store.put(
        user_id,
        snapshot.model_copy(
            update={"publish_results": results, "approval_requests": remaining}
        ),
    )
