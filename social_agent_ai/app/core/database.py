"""Async SQLAlchemy engine and session handling.

The agent pipeline itself holds no database session — nodes talk to the token
store and the run store — so this exists for the persistence layer behind
those stores, and for Alembic.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional

from app.core.config import Settings, get_settings

log = logging.getLogger(__name__)

_engine: Optional["object"] = None
_session_factory: Optional["object"] = None


def get_engine(settings: Optional[Settings] = None):  # type: ignore[no-untyped-def]
    """Lazily build the engine so importing this module needs no database."""
    global _engine
    if _engine is None:
        from sqlalchemy.ext.asyncio import create_async_engine

        settings = settings or get_settings()
        _engine = create_async_engine(
            settings.database_url,
            pool_size=settings.database_pool_size,
            pool_pre_ping=True,
            echo=settings.debug and not settings.is_production,
        )
    return _engine


def get_session_factory(settings: Optional[Settings] = None):  # type: ignore[no-untyped-def]
    global _session_factory
    if _session_factory is None:
        from sqlalchemy.ext.asyncio import async_sessionmaker

        _session_factory = async_sessionmaker(
            bind=get_engine(settings), expire_on_commit=False
        )
    return _session_factory


@asynccontextmanager
async def session_scope() -> AsyncIterator["object"]:
    """Transactional scope: commit on success, roll back on failure."""
    factory = get_session_factory()
    async with factory() as session:  # type: ignore[operator]
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def check_connection() -> bool:
    """Used by the readiness probe; never raises."""
    try:
        from sqlalchemy import text

        engine = get_engine()
        async with engine.connect() as connection:  # type: ignore[attr-defined]
            await connection.execute(text("SELECT 1"))
        return True
    except Exception as exc:  # noqa: BLE001
        log.warning("database check failed: %s", exc)
        return False


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()  # type: ignore[attr-defined]
    _engine = None
    _session_factory = None
