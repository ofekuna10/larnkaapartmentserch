"""Analytics Agent — fetch history, compute the numbers, then interpret them.

Split deliberately in two halves:

1. **Deterministic aggregation** (``aggregate_platform``): every number the
   pipeline downstream relies on is computed in Python. Engagement rates,
   retention, best posting windows and format performance are arithmetic, not
   opinions, and must be reproducible.
2. **Interpretation** (one structured LLM call): highlights, weaknesses and
   content themes. If the call fails, :func:`heuristic_insight` produces the
   same shape from the aggregates, so the run continues.
"""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from statistics import mean, median
from typing import Any, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agents.nodes._common import agent_node
from app.agents.prompts import ANALYTICS_SYSTEM
from app.agents.state import AgentState, NodeName, active_platforms
from app.core.config import get_settings
from app.core.llm import LLMRequest, get_llm_client
from app.models.schemas import (
    AnalyticsSummary,
    ExecutionStatus,
    PipelineError,
    Platform,
    PlatformAnalytics,
    PostingWindow,
    PostMetrics,
)
from app.services.base import OAuthToken, SocialAPIError, TokenExpiredError
from app.services.registry import connector_for, get_token_store
from app.services.sandbox import sandbox_token

log = logging.getLogger(__name__)

TOP_N = 3
MIN_WINDOW_SAMPLES = 2


class AnalyticsInsight(BaseModel):
    """The interpretive half of the summary — the only part an LLM writes."""

    model_config = ConfigDict(extra="forbid")

    narrative: str = Field(description="Two or three sentences of plain-English read")
    highlights: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    winning_topics: list[str] = Field(default_factory=list)
    underperforming_topics: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Deterministic aggregation
# ---------------------------------------------------------------------------
def aggregate_platform(platform: Platform, posts: Sequence[PostMetrics]) -> PlatformAnalytics:
    """Roll a platform's post list into one comparable summary."""
    if not posts:
        return PlatformAnalytics(platform=platform)

    rates = [p.engagement_rate for p in posts if p.engagement_rate is not None]
    retentions = [p.retention_rate for p in posts if p.retention_rate is not None]

    by_format: dict[str, list[float]] = defaultdict(list)
    for post in posts:
        if post.content_format and post.engagement_rate is not None:
            by_format[post.content_format.value].append(post.engagement_rate)

    ranked = sorted(
        (p for p in posts if p.engagement_rate is not None),
        key=lambda p: p.engagement_rate or 0.0,
        reverse=True,
    )

    return PlatformAnalytics(
        platform=platform,
        posts_analyzed=len(posts),
        total_views=sum(p.views for p in posts),
        total_impressions=sum(p.impressions for p in posts),
        avg_engagement_rate=round(mean(rates), 6) if rates else None,
        median_engagement_rate=round(median(rates), 6) if rates else None,
        avg_retention_rate=round(mean(retentions), 6) if retentions else None,
        follower_growth=sum(p.follows for p in posts) or None,
        best_windows=best_posting_windows(posts),
        top_posts=list(ranked[:TOP_N]),
        worst_posts=list(reversed(ranked[-TOP_N:])) if len(ranked) > TOP_N else [],
        format_performance={
            fmt: round(mean(values), 6) for fmt, values in sorted(by_format.items())
        },
    )


def best_posting_windows(
    posts: Sequence[PostMetrics], *, limit: int = 3
) -> list[PostingWindow]:
    """Weekday/hour slots whose posts beat the account's own average.

    Slots with fewer than ``MIN_WINDOW_SAMPLES`` posts are dropped — a single
    lucky post is not a posting time.
    """
    buckets: dict[tuple[int, int], list[float]] = defaultdict(list)
    for post in posts:
        if post.published_at is None or post.engagement_rate is None:
            continue
        buckets[(post.published_at.weekday(), post.published_at.hour)].append(
            post.engagement_rate
        )

    windows = [
        PostingWindow(
            weekday=weekday,
            hour_utc=hour,
            avg_engagement_rate=round(mean(values), 6),
            sample_size=len(values),
        )
        for (weekday, hour), values in buckets.items()
        if len(values) >= MIN_WINDOW_SAMPLES
    ]
    if not windows:  # sparse history: fall back to single-sample slots
        windows = [
            PostingWindow(
                weekday=weekday,
                hour_utc=hour,
                avg_engagement_rate=round(mean(values), 6),
                sample_size=len(values),
            )
            for (weekday, hour), values in buckets.items()
        ]
    windows.sort(key=lambda w: (w.avg_engagement_rate, w.sample_size), reverse=True)
    return windows[:limit]


def heuristic_insight(summary: AnalyticsSummary) -> AnalyticsInsight:
    """Findings derived arithmetically — the fallback when the LLM is unavailable."""
    highlights: list[str] = []
    weaknesses: list[str] = []
    winning: list[str] = []
    losing: list[str] = []

    for name, platform in summary.per_platform.items():
        if not platform.posts_analyzed:
            weaknesses.append(f"No posts on {name} in the last {summary.lookback_days} days.")
            continue
        if platform.format_performance:
            best_format = max(platform.format_performance.items(), key=lambda kv: kv[1])
            worst_format = min(platform.format_performance.items(), key=lambda kv: kv[1])
            highlights.append(
                f"{name}: {best_format[0]} averages "
                f"{best_format[1] * 100:.1f}% engagement, the strongest format."
            )
            if worst_format[0] != best_format[0]:
                weaknesses.append(
                    f"{name}: {worst_format[0]} trails at "
                    f"{worst_format[1] * 100:.1f}% engagement."
                )
        if platform.best_windows:
            window = platform.best_windows[0]
            highlights.append(
                f"{name}: posts published around {window.hour_utc:02d}:00 UTC on "
                f"weekday {window.weekday} perform best."
            )
        winning.extend(post.title for post in platform.top_posts[:2] if post.title)
        losing.extend(post.title for post in platform.worst_posts[:1] if post.title)
        if (
            platform.avg_retention_rate is not None
            and platform.avg_retention_rate < 0.35
        ):
            weaknesses.append(
                f"{name}: average retention is {platform.avg_retention_rate * 100:.0f}%"
                " — openings are losing viewers."
            )

    covered = ", ".join(summary.platforms_covered) or "no platforms"
    return AnalyticsInsight(
        narrative=(
            f"Computed over {summary.lookback_days} days across {covered}. "
            f"{len(highlights)} strength(s) and {len(weaknesses)} gap(s) identified "
            "from measured engagement, retention and posting time."
        ),
        highlights=highlights[:6],
        weaknesses=weaknesses[:6],
        winning_topics=winning[:5],
        underperforming_topics=losing[:5],
    )


def _render_for_prompt(summary: AnalyticsSummary) -> str:
    lines: list[str] = [f"Lookback window: {summary.lookback_days} days"]
    for name, platform in summary.per_platform.items():
        lines.append(f"\n## {name}")
        lines.append(f"- posts analysed: {platform.posts_analyzed}")
        lines.append(f"- total views: {platform.total_views}")
        if platform.avg_engagement_rate is not None:
            lines.append(
                f"- engagement rate: avg {platform.avg_engagement_rate:.4f}, "
                f"median {platform.median_engagement_rate:.4f}"
            )
        if platform.avg_retention_rate is not None:
            lines.append(f"- avg retention: {platform.avg_retention_rate:.3f}")
        if platform.format_performance:
            formats = ", ".join(
                f"{fmt} {rate:.4f}" for fmt, rate in platform.format_performance.items()
            )
            lines.append(f"- engagement by format: {formats}")
        for window in platform.best_windows:
            lines.append(
                f"- strong slot: weekday {window.weekday} at {window.hour_utc:02d}:00 UTC "
                f"({window.avg_engagement_rate:.4f} over {window.sample_size} posts)"
            )
        for post in platform.top_posts:
            lines.append(
                f"- top: {post.title!r} views={post.views} "
                f"er={post.engagement_rate or 0:.4f}"
            )
        for post in platform.worst_posts:
            lines.append(
                f"- weak: {post.title!r} views={post.views} "
                f"er={post.engagement_rate or 0:.4f}"
            )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Fetching
# ---------------------------------------------------------------------------
async def _token_for(user_id: str, platform: Platform) -> OAuthToken:
    """Decrypted credential for a platform, refreshed if it has expired."""
    store = get_token_store()
    token = await store.get(user_id, platform)
    if token is None:
        # No stored credential: the sandbox connector is what will be used.
        return sandbox_token(platform, user_id)
    if token.is_expired:
        connector = connector_for(platform)
        token = await connector.refresh_token(token)
        await store.put(user_id, token)
    return token


async def fetch_platform_metrics(
    user_id: str, platform: Platform, lookback_days: int
) -> list[PostMetrics]:
    token = await _token_for(user_id, platform)
    connector = connector_for(platform)
    return await connector.fetch_recent_posts(token, lookback_days=lookback_days)


@agent_node(NodeName.ANALYTICS, ExecutionStatus.ANALYZING)
async def analytics_node(state: AgentState) -> dict[str, Any]:
    settings = get_settings()
    user_id = state["user_id"]
    lookback = settings.analytics_lookback_days
    platforms = active_platforms(state)

    results = await asyncio.gather(
        *(fetch_platform_metrics(user_id, p, lookback) for p in platforms),
        return_exceptions=True,
    )

    all_posts: list[PostMetrics] = []
    per_platform: dict[str, PlatformAnalytics] = {}
    errors: list[PipelineError] = []

    for platform, result in zip(platforms, results, strict=True):
        if isinstance(result, BaseException):
            recoverable = not isinstance(result, TokenExpiredError)
            message = (
                str(result)
                if isinstance(result, SocialAPIError)
                else f"{platform.value} metrics fetch failed: {result}"
            )
            log.warning("analytics.partial_failure %s: %s", platform.value, result)
            errors.append(
                PipelineError(
                    node=NodeName.ANALYTICS, message=message, recoverable=recoverable
                )
            )
            per_platform[platform.value] = PlatformAnalytics(platform=platform)
            continue
        all_posts.extend(result)
        per_platform[platform.value] = aggregate_platform(platform, result)

    if not all_posts:
        # Nothing to interpret. Report it as a run-level failure rather than
        # letting the strategy agent invent a plan out of nothing.
        return {
            "execution_status": ExecutionStatus.FAILED,
            "raw_metrics": [],
            "raw_analytics": AnalyticsSummary(
                lookback_days=lookback, per_platform=per_platform
            ),
            "errors": [
                *errors,
                PipelineError(
                    node=NodeName.ANALYTICS,
                    message="no posts available on any connected platform",
                    recoverable=False,
                ),
            ],
        }

    summary = AnalyticsSummary(lookback_days=lookback, per_platform=per_platform)
    insight = await _interpret(summary)
    summary = summary.model_copy(
        update={
            "narrative": insight.narrative,
            "highlights": insight.highlights,
            "weaknesses": insight.weaknesses,
            "winning_topics": insight.winning_topics,
            "underperforming_topics": insight.underperforming_topics,
        }
    )

    update: dict[str, Any] = {
        "raw_metrics": all_posts,
        "raw_analytics": summary,
        "next_node": NodeName.STRATEGY,
        "_detail": f"{len(all_posts)} posts across {len(per_platform)} platform(s)",
    }
    if errors:
        update["errors"] = errors
    return update


async def _interpret(summary: AnalyticsSummary) -> AnalyticsInsight:
    llm = get_llm_client()
    request = LLMRequest(
        intent="analytics.interpret",
        system=ANALYTICS_SYSTEM,
        prompt=(
            "Here are the computed metrics for the account.\n\n"
            f"{_render_for_prompt(summary)}\n\n"
            "Produce the findings. Use only these numbers."
        ),
        fallback=lambda: heuristic_insight(summary),
    )
    return await llm.parse(request, AnalyticsInsight)
