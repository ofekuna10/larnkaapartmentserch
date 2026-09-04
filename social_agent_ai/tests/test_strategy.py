"""Strategy: schedule arithmetic and coercion of loose LLM output."""

from __future__ import annotations

from datetime import datetime, timezone

from app.agents.nodes.analytics import aggregate_platform
from app.agents.nodes.strategy import (
    _coerce_format,
    _coerce_platform,
    _next_window_occurrence,
    build_schedule,
    heuristic_strategy,
)
from app.models.schemas import (
    AnalyticsSummary,
    ContentFormat,
    ContentRecommendation,
    Platform,
    UserGoals,
)
from tests.conftest import make_metrics

NOW = datetime(2026, 3, 2, 8, 0, tzinfo=timezone.utc)  # a Monday


def test_next_window_occurrence_moves_forward():
    # Wednesday (weekday 2) at 17:00 UTC, from Monday 08:00.
    slot = _next_window_occurrence(NOW, weekday=2, hour=17)
    assert slot == datetime(2026, 3, 4, 17, 0, tzinfo=timezone.utc)

    # Asking for the current weekday at an hour already past rolls a week on.
    slot = _next_window_occurrence(NOW, weekday=0, hour=7)
    assert slot == datetime(2026, 3, 9, 7, 0, tzinfo=timezone.utc)


def test_build_schedule_uses_measured_windows():
    posts = [
        make_metrics(post_id="a", days_ago=7, hour=17),
        make_metrics(post_id="b", days_ago=14, hour=17),
    ]
    analytics = AnalyticsSummary(
        per_platform={"instagram": aggregate_platform(Platform.INSTAGRAM, posts)}
    )
    recommendation = ContentRecommendation(
        title="One", platform=Platform.INSTAGRAM,
        content_format=ContentFormat.SHORT_VIDEO,
    )
    slots = build_schedule([recommendation], analytics, UserGoals(), now=NOW)

    assert len(slots) == 1
    assert slots[0].publish_at.hour == 17
    assert slots[0].recommendation_id == recommendation.recommendation_id


def test_build_schedule_never_double_books_a_slot():
    posts = [
        make_metrics(post_id="a", days_ago=7, hour=17),
        make_metrics(post_id="b", days_ago=14, hour=17),
    ]
    analytics = AnalyticsSummary(
        per_platform={"instagram": aggregate_platform(Platform.INSTAGRAM, posts)}
    )
    recommendations = [
        ContentRecommendation(
            title=f"Post {index}", platform=Platform.INSTAGRAM,
            content_format=ContentFormat.SHORT_VIDEO,
        )
        for index in range(3)
    ]
    slots = build_schedule(recommendations, analytics, UserGoals(), now=NOW)

    stamps = [slot.publish_at for slot in slots]
    assert len(set(stamps)) == len(stamps)
    assert stamps == sorted(stamps)


def test_build_schedule_falls_back_without_analytics():
    recommendations = [
        ContentRecommendation(
            title="Only", platform=Platform.TIKTOK,
            content_format=ContentFormat.SHORT_VIDEO,
        )
    ]
    slots = build_schedule(recommendations, None, UserGoals(horizon_days=7), now=NOW)
    assert len(slots) == 1
    assert slots[0].publish_at > NOW


def test_coerce_platform_rejects_unconnected_and_unknown():
    allowed = [Platform.INSTAGRAM]
    assert _coerce_platform("instagram", allowed) is Platform.INSTAGRAM
    assert _coerce_platform("INSTAGRAM ", allowed) is Platform.INSTAGRAM
    assert _coerce_platform("tiktok", allowed) is None
    assert _coerce_platform("myspace", allowed) is None


def test_coerce_format_snaps_onto_a_supported_format():
    # TikTok has no text post; the coercion picks its first supported format.
    assert _coerce_format("text_post", Platform.TIKTOK) is ContentFormat.SHORT_VIDEO
    assert _coerce_format("nonsense", Platform.YOUTUBE) in set(ContentFormat)
    assert (
        _coerce_format("carousel", Platform.INSTAGRAM) is ContentFormat.CAROUSEL
    )


def test_heuristic_strategy_respects_posts_per_platform():
    posts = [make_metrics(post_id=f"p{i}", days_ago=i + 1) for i in range(4)]
    analytics = AnalyticsSummary(
        per_platform={"instagram": aggregate_platform(Platform.INSTAGRAM, posts)}
    )
    draft = heuristic_strategy(
        analytics, UserGoals(posts_per_platform=2), [Platform.INSTAGRAM]
    )

    assert len(draft.recommendations) == 2
    assert all(rec.platform == "instagram" for rec in draft.recommendations)
    assert draft.topic_clusters
