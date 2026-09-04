"""Shared scaffolding for agent nodes: tracing, timing and error containment."""

from __future__ import annotations

import functools
import logging
import time
from typing import Any, Awaitable, Callable

from app.agents.state import AgentState, NodeVisit
from app.models.schemas import ExecutionStatus, PipelineError, utcnow

log = logging.getLogger(__name__)

NodeFn = Callable[[AgentState], Awaitable[dict[str, Any]]]


def agent_node(name: str, status: ExecutionStatus) -> Callable[[NodeFn], NodeFn]:
    """Wrap a node with the control-plane bookkeeping every node owes.

    The wrapper stamps ``current_node`` / ``execution_status`` / ``updated_at``,
    appends a :class:`NodeVisit`, and converts an unhandled exception into a
    recorded :class:`PipelineError` plus ``execution_status=FAILED`` — so a
    crashed node produces an inspectable run instead of a stack trace on the
    request path.
    """

    def decorator(fn: NodeFn) -> NodeFn:
        @functools.wraps(fn)
        async def wrapper(state: AgentState) -> dict[str, Any]:
            started = time.perf_counter()
            log.info("node.start %s run=%s", name, state.get("run_id"))
            try:
                update = await fn(state)
            except Exception as exc:
                elapsed = (time.perf_counter() - started) * 1000
                log.exception("node.error %s run=%s", name, state.get("run_id"))
                return {
                    "current_node": name,
                    "execution_status": ExecutionStatus.FAILED,
                    "updated_at": utcnow(),
                    "errors": [
                        PipelineError(node=name, message=str(exc), recoverable=False)
                    ],
                    "node_trace": [
                        NodeVisit(
                            node=name,
                            duration_ms=round(elapsed, 2),
                            status="error",
                            detail=str(exc)[:500],
                        )
                    ],
                }

            elapsed = (time.perf_counter() - started) * 1000
            update.setdefault("current_node", name)
            update.setdefault("execution_status", status)
            update["updated_at"] = utcnow()
            detail = str(update.pop("_detail", ""))
            update.setdefault(
                "node_trace",
                [NodeVisit(node=name, duration_ms=round(elapsed, 2), detail=detail)],
            )
            log.info("node.done %s in %.0fms", name, elapsed)
            return update

        return wrapper

    return decorator
