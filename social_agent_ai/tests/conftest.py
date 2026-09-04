"""Test fixtures.

Every test runs fully offline: the ``echo`` LLM provider, the in-memory brand
voice store and the sandbox connectors. Nothing here reaches the network.
"""

from __future__ import annotations

import os
from datetime import timedelta
from typing import Iterator

import pytest

# Must be set before the first `Settings()` instantiation.
os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "echo")
os.environ.setdefault("VECTOR_BACKEND", "memory")
os.environ.setdefault(
    "SECRET_KEY", "test-secret-key-for-unit-tests-0123456789abcdef"
)
os.environ.setdefault("AUTO_PUBLISH_ENABLED", "true")

from app.core import llm as llm_module
from app.core.config import get_settings
from app.core.security import get_token_cipher
from app.models.schemas import (
    ContentDraft,
    ContentFormat,
    HashtagStrategy,
    Platform,
    PlatformConnection,
    PostMetrics,
    ScriptBeat,
    utcnow,
)
from app.services import registry, run_store, vector_store


@pytest.fixture(autouse=True)
def _clean_state() -> Iterator[None]:
    """Reset every process-wide singleton between tests."""
    get_settings.cache_clear()
    get_token_cipher.cache_clear()
    llm_module.reset_llm_clients()
    registry.set_connector_factory(None)
    registry.set_token_store(None)
    run_store.set_run_store(None)
    vector_store.set_brand_voice_store(None)
    yield
    llm_module.reset_llm_clients()
    registry.set_connector_factory(None)
    registry.set_token_store(None)
    run_store.set_run_store(None)
    vector_store.set_brand_voice_store(None)


@pytest.fixture
def settings():
    return get_settings()


@pytest.fixture
def echo_llm() -> llm_module.EchoLLMClient:
    """An echo client installed as the active provider, ready for fixtures."""
    client = llm_module.EchoLLMClient()
    llm_module.set_llm_client(client)
    return client


@pytest.fixture
def connections() -> list[PlatformConnection]:
    return [
        PlatformConnection(platform=Platform.INSTAGRAM, account_id="ig-1"),
        PlatformConnection(platform=Platform.YOUTUBE, account_id="yt-1"),
    ]


@pytest.fixture
def draft() -> ContentDraft:
    """A clean, compliant Instagram Reel draft."""
    return ContentDraft(
        platform=Platform.INSTAGRAM,
        content_format=ContentFormat.SHORT_VIDEO,
        title="How we cut onboarding to one day",
        hook="Onboarding used to take us two weeks.",
        body="We removed three approvals and wrote the checklist down. That was it.",
        call_to_action="Ask for the checklist in the comments.",
        hashtags=HashtagStrategy(broad=["operations"], niche=["onboarding", "saasops"]),
        media_brief="Talking head over screen recording, 9:16.",
        script=[
            ScriptBeat(start_seconds=0, end_seconds=4, visual="Open on the calendar"),
            ScriptBeat(start_seconds=4, end_seconds=14, visual="Show the checklist"),
            ScriptBeat(start_seconds=14, end_seconds=20, visual="Close on the ask"),
        ],
    )


def make_metrics(
    platform: Platform = Platform.INSTAGRAM,
    *,
    post_id: str = "p1",
    impressions: int = 1000,
    likes: int = 50,
    comments: int = 5,
    shares: int = 3,
    saves: int = 2,
    days_ago: float = 1.0,
    hour: int | None = None,
    content_format: ContentFormat = ContentFormat.SHORT_VIDEO,
    duration_seconds: float | None = 30.0,
    avg_view_duration_seconds: float | None = 15.0,
) -> PostMetrics:
    """Build one metrics row with sane defaults; overrides drive the assertions."""
    published = utcnow() - timedelta(days=days_ago)
    if hour is not None:
        published = published.replace(hour=hour, minute=0, second=0, microsecond=0)
    return PostMetrics(
        post_id=post_id,
        platform=platform,
        published_at=published,
        content_format=content_format,
        impressions=impressions,
        likes=likes,
        comments=comments,
        shares=shares,
        saves=saves,
        duration_seconds=duration_seconds,
        avg_view_duration_seconds=avg_view_duration_seconds,
    )
