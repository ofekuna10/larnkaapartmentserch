"""Where pipeline runs are recorded while and after they execute.

The API needs three things: start a run in the background, poll it, and act on
what it produced. That is all this store does. Swap the in-memory
implementation for Postgres (one row per run, snapshot as JSONB) in a
multi-worker deployment — the protocol is the contract the routers use.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional, Protocol

from app.agents.state import PipelineSnapshot
from app.models.schemas import ExecutionStatus

log = logging.getLogger(__name__)


class RunStore(Protocol):
    async def put(self, user_id: str, snapshot: PipelineSnapshot) -> None: ...

    async def get(self, user_id: str, run_id: str) -> Optional[PipelineSnapshot]: ...

    async def list(self, user_id: str, *, limit: int = 20) -> list[PipelineSnapshot]: ...


class InMemoryRunStore:
    def __init__(self) -> None:
        self._runs: dict[tuple[str, str], PipelineSnapshot] = {}
        self._order: list[tuple[str, str]] = []
        self._lock = asyncio.Lock()

    async def put(self, user_id: str, snapshot: PipelineSnapshot) -> None:
        key = (user_id, snapshot.run_id)
        async with self._lock:
            if key not in self._runs:
                self._order.append(key)
            self._runs[key] = snapshot

    async def get(self, user_id: str, run_id: str) -> Optional[PipelineSnapshot]:
        return self._runs.get((user_id, run_id))

    async def list(self, user_id: str, *, limit: int = 20) -> list[PipelineSnapshot]:
        keys = [key for key in reversed(self._order) if key[0] == user_id]
        return [self._runs[key] for key in keys[:limit]]


_store: Optional[RunStore] = None
# Strong references to in-flight tasks; without this the event loop may drop
# a background run mid-execution.
_tasks: set[asyncio.Task[None]] = set()


def get_run_store() -> RunStore:
    global _store
    if _store is None:
        _store = InMemoryRunStore()
    return _store


def set_run_store(store: Optional[RunStore]) -> None:
    global _store
    _store = store


def track(task: "asyncio.Task[None]") -> None:
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


def in_flight() -> int:
    return sum(1 for task in _tasks if not task.done())


def failed_snapshot(
    user_id: str, run_id: str, message: str
) -> PipelineSnapshot:
    """A snapshot for a run that died before the graph could record anything."""
    from app.models.schemas import PipelineError

    return PipelineSnapshot(
        run_id=run_id,
        user_id=user_id,
        execution_status=ExecutionStatus.FAILED,
        errors=[PipelineError(node="api", message=message, recoverable=False)],
    )
