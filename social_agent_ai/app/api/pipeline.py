"""Pipeline endpoints: start a run, poll it, inspect the graph."""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.agents.graph import pipeline_diagram, run_pipeline
from app.agents.state import PipelineSnapshot
from app.api.deps import AppSettings, CurrentUser
from app.models.api import (
    DraftListResponse,
    PipelineRunAccepted,
    PipelineRunRequest,
    RunListItem,
)
from app.models.schemas import ExecutionStatus, PlatformConnection, new_id
from app.services.registry import get_token_store
from app.services.run_store import failed_snapshot, get_run_store, track

log = logging.getLogger(__name__)

router = APIRouter(prefix="/pipeline", tags=["pipeline"])


async def _connections(user_id: str, request: PipelineRunRequest) -> list[PlatformConnection]:
    """Connections to run against, narrowed by the request's platform filter."""
    connections = await get_token_store().connections(user_id)
    if not connections:
        # No stored credentials: run against every platform the caller asked
        # for, which the registry serves from the sandbox connector.
        wanted = request.platforms or request.goals.target_platforms
        connections = [
            PlatformConnection(
                platform=platform, account_id=f"{platform.value}-{user_id}"
            )
            for platform in wanted
        ]
    if request.platforms:
        connections = [c for c in connections if c.platform in request.platforms]
    return connections


@router.post(
    "/run",
    response_model=None,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Run the multi-agent pipeline",
)
async def start_run(
    body: PipelineRunRequest,
    user_id: CurrentUser,
    settings: AppSettings,
    response: Response,
) -> PipelineRunAccepted | PipelineSnapshot:
    """Trigger the LangGraph workflow.

    Returns ``202`` with a ``run_id`` immediately and executes the graph in the
    background; pass ``wait=true`` to block and receive the finished snapshot
    instead (useful in tests and for short runs).
    """
    connections = await _connections(user_id, body)
    if not connections:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "no connected platforms; connect an account or name platforms "
                "in the request body"
            ),
        )

    goals = body.goals
    if body.platforms and not goals.target_platforms:
        goals = goals.model_copy(update={"target_platforms": body.platforms})

    run_id = new_id("run")
    store = get_run_store()

    async def execute() -> PipelineSnapshot:
        try:
            snapshot = await run_pipeline(
                user_id=user_id,
                connected_platforms=connections,
                goals=goals,
                run_id=run_id,
                metadata=body.metadata,
            )
        except Exception as exc:
            log.exception("pipeline run %s crashed", run_id)
            snapshot = failed_snapshot(user_id, run_id, str(exc))
        await store.put(user_id, snapshot)
        return snapshot

    if body.wait:
        response.status_code = status.HTTP_200_OK
        return await execute()

    # Record the run as pending so an immediate poll finds it.
    await store.put(
        user_id,
        PipelineSnapshot(
            run_id=run_id,
            user_id=user_id,
            execution_status=ExecutionStatus.PENDING,
            max_retries=settings.max_validation_retries,
        ),
    )
    track(asyncio.create_task(_run_and_discard(execute)))
    return PipelineRunAccepted(
        run_id=run_id,
        execution_status=ExecutionStatus.PENDING,
        poll_url=f"{settings.api_v1_prefix}/pipeline/runs/{run_id}",
    )


async def _run_and_discard(execute) -> None:  # type: ignore[no-untyped-def]
    await execute()


@router.get("/runs", response_model=list[RunListItem], summary="List recent runs")
async def list_runs(
    user_id: CurrentUser, limit: int = Query(default=20, ge=1, le=100)
) -> list[RunListItem]:
    snapshots = await get_run_store().list(user_id, limit=limit)
    return [
        RunListItem(
            run_id=snapshot.run_id,
            execution_status=snapshot.execution_status,
            current_node=snapshot.current_node,
            started_at=snapshot.started_at,
            updated_at=snapshot.updated_at,
        )
        for snapshot in snapshots
    ]


@router.get(
    "/runs/{run_id}", response_model=PipelineSnapshot, summary="Fetch one run"
)
async def get_run(run_id: str, user_id: CurrentUser) -> PipelineSnapshot:
    snapshot = await get_run_store().get(user_id, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown run {run_id}"
        )
    return snapshot


@router.get(
    "/runs/{run_id}/drafts",
    response_model=DraftListResponse,
    summary="Drafts produced by a run",
)
async def get_run_drafts(run_id: str, user_id: CurrentUser) -> DraftListResponse:
    snapshot = await get_run_store().get(user_id, run_id)
    if snapshot is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown run {run_id}"
        )
    return DraftListResponse(run_id=run_id, drafts=snapshot.generated_content)


@router.get("/graph", summary="Mermaid diagram of the compiled graph")
async def get_graph() -> dict[str, str]:
    return {"format": "mermaid", "diagram": pipeline_diagram()}
