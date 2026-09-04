"""Deterministic analytics: the numbers must be arithmetic, not opinion."""

from __future__ import annotations

import pytest

from app.agents.nodes.analytics import (
    aggregate_platform,
    best_posting_windows,
    heuristic_insight,
)
from app.models.schemas import AnalyticsSummary, ContentFormat, Platform
from tests.conftest import make_metrics


def test_engagement_rate_uses_impressions_first():
    post = make_metrics(impressions=1000, likes=50, comments=5, shares=3, saves=2)
    assert post.engagement_rate == pytest.approx(0.06)


def test_engagement_rate_falls_back_to_reach_then_views():
    post = make_metrics(impressions=0, likes=10, comments=0, shares=0, saves=0)
    post.reach = 200
    assert post.engagement_rate == pytest.approx(0.05)

    post.reach = 0
    post.views = 100
    assert post.engagement_rate == pytest.approx(0.1)

    post.views = 0
    assert post.engagement_rate is None


def test_retention_rate_is_capped_at_one():
    post = make_metrics(duration_seconds=30, avg_view_duration_seconds=45)
    assert post.retention_rate == 1.0


def test_aggregate_platform_computes_mean_median_and_top_posts():
    posts = [
        make_metrics(post_id="a", impressions=1000, likes=100, comments=0, shares=0, saves=0),
        make_metrics(post_id="b", impressions=1000, likes=50, comments=0, shares=0, saves=0),
        make_metrics(post_id="c", impressions=1000, likes=10, comments=0, shares=0, saves=0),
    ]
    stats = aggregate_platform(Platform.INSTAGRAM, posts)

    assert stats.posts_analyzed == 3
    assert stats.total_impressions == 3000
    # Aggregates are rounded to 6 dp, so compare with that tolerance.
    assert stats.avg_engagement_rate == pytest.approx(
        (0.10 + 0.05 + 0.01) / 3, abs=1e-6
    )
    assert stats.median_engagement_rate == pytest.approx(0.05)
    assert [p.post_id for p in stats.top_posts] == ["a", "b", "c"]


def test_aggregate_platform_handles_empty_history():
    stats = aggregate_platform(Platform.TIKTOK, [])
    assert stats.posts_analyzed == 0
    assert stats.avg_engagement_rate is None
    assert stats.best_windows == []


def test_format_performance_separates_formats():
    posts = [
        make_metrics(
            post_id="s1", content_format=ContentFormat.SHORT_VIDEO,
            impressions=1000, likes=80, comments=0, shares=0, saves=0,
        ),
        make_metrics(
            post_id="i1", content_format=ContentFormat.IMAGE_POST,
            impressions=1000, likes=20, comments=0, shares=0, saves=0,
        ),
    ]
    stats = aggregate_platform(Platform.INSTAGRAM, posts)
    assert stats.format_performance["short_video"] == pytest.approx(0.08)
    assert stats.format_performance["image_post"] == pytest.approx(0.02)


def test_best_windows_prefers_repeated_slots_over_lucky_singles():
    # Two posts at 09:00 with solid rates, one outlier at 03:00 with a great one.
    posts = [
        make_metrics(post_id="a", days_ago=7, hour=9, impressions=1000, likes=60,
                     comments=0, shares=0, saves=0),
        make_metrics(post_id="b", days_ago=14, hour=9, impressions=1000, likes=60,
                     comments=0, shares=0, saves=0),
        make_metrics(post_id="c", days_ago=10, hour=3, impressions=1000, likes=200,
                     comments=0, shares=0, saves=0),
    ]
    windows = best_posting_windows(posts)

    # The single-sample 03:00 slot is discarded entirely.
    assert [w.hour_utc for w in windows] == [9]
    assert windows[0].sample_size == 2


def test_best_windows_falls_back_when_history_is_sparse():
    posts = [make_metrics(post_id="a", days_ago=3, hour=17)]
    windows = best_posting_windows(posts)
    assert len(windows) == 1
    assert windows[0].hour_utc == 17


def test_heuristic_insight_reads_the_aggregates():
    posts = [
        make_metrics(post_id="s1", content_format=ContentFormat.SHORT_VIDEO,
                     impressions=1000, likes=90, comments=0, shares=0, saves=0),
        make_metrics(post_id="i1", content_format=ContentFormat.IMAGE_POST,
                     impressions=1000, likes=10, comments=0, shares=0, saves=0),
    ]
    summary = AnalyticsSummary(
        per_platform={"instagram": aggregate_platform(Platform.INSTAGRAM, posts)}
    )
    insight = heuristic_insight(summary)

    assert any("short_video" in item for item in insight.highlights)
    assert any("image_post" in item for item in insight.weaknesses)
    assert insight.narrative
