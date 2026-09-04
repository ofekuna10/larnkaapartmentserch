"""Strategy Agent — turn the performance summary and the goals into a plan.

The LLM decides *what* to make (topic clusters, angles, formats, priority).
The schedule is computed here, from the posting windows the Analytics Agent
measured: an LLM guessing at timestamps is strictly worse than arithmetic over
the account's own history.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agents.nodes._common import agent_node
from app.agents.prompts import STRATEGY_SYSTEM
from app.agents.state import AgentState, NodeName, active_platforms
from app.core.llm import LLMRequest, get_llm_client
from app.core.platform_rules import rules_for
from app.models.schemas import (
    AnalyticsSummary,
    ContentFormat,
    ContentRecommendation,
    ExecutionStatus,
    Platform,
    ScheduleSlot,
    StrategyPlan,
    TopicCluster,
    UserGoals,
    utcnow,
)

log = logging.getLogger(__name__)


class DraftTopicCluster(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    rationale: str = ""
    keywords: list[str] = Field(default_factory=list)
    platforms: list[str] = Field(default_factory=list)
    expected_lift: Optional[float] = None


class DraftRecommendation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    angle: str = ""
    platform: str
    content_format: str
    topic_cluster: str = ""
    priority: int = 3
    reasoning: str = ""


class StrategyDraft(BaseModel):
    """The LLM's contract — loose strings, validated on the way in."""

    model_config = ConfigDict(extra="forbid")

    objective: str = ""
    notes: str = ""
    topic_clusters: list[DraftTopicCluster] = Field(default_factory=list)
    recommendations: list[DraftRecommendation] = Field(default_factory=list)


def _coerce_platform(value: str, allowed: Sequence[Platform]) -> Optional[Platform]:
    try:
        platform = Platform(value.strip().lower())
    except ValueError:
        return None
    return platform if platform in allowed else None


def _coerce_format(value: str, platform: Platform) -> ContentFormat:
    """Snap a suggested format onto something the platform actually supports."""
    rules = rules_for(platform)
    try:
        content_format = ContentFormat(value.strip().lower())
    except ValueError:
        content_format = rules.supported_formats[0]
    if not rules.supports(content_format):
        content_format = rules.supported_formats[0]
    return content_format


def build_schedule(
    recommendations: Sequence[ContentRecommendation],
    analytics: Optional[AnalyticsSummary],
    goals: UserGoals,
    *,
    now: Optional[datetime] = None,
) -> list[ScheduleSlot]:
    """Place each recommendation in the next matching high-performing window.

    Falls back to a fixed cadence when a platform has no measured window, and
    never schedules two posts for the same platform in the same slot.
    """
    now = now or utcnow()
    horizon_end = now + timedelta(days=goals.horizon_days)
    taken: set[tuple[Platform, datetime]] = set()
    slots: list[ScheduleSlot] = []

    per_platform_index: dict[Platform, int] = {}
    for recommendation in recommendations:
        platform = recommendation.platform
        index = per_platform_index.get(platform, 0)
        per_platform_index[platform] = index + 1

        windows = []
        if analytics and platform.value in analytics.per_platform:
            windows = analytics.per_platform[platform.value].best_windows

        publish_at: Optional[datetime] = None
        if windows:
            window = windows[index % len(windows)]
            publish_at = _next_window_occurrence(
                now + timedelta(days=index // max(len(windows), 1)), window.weekday,
                window.hour_utc,
            )
        if publish_at is None or publish_at > horizon_end:
            # Even cadence across the horizon as a fallback.
            step = max(goals.horizon_days // max(len(recommendations), 1), 1)
            publish_at = (now + timedelta(days=step * (len(slots) + 1))).replace(
                minute=0, second=0, microsecond=0
            )

        while (platform, publish_at) in taken:
            publish_at += timedelta(days=1)
        taken.add((platform, publish_at))

        slots.append(
            ScheduleSlot(
                platform=platform,
                publish_at=publish_at,
                recommendation_id=recommendation.recommendation_id,
                content_format=recommendation.content_format,
            )
        )
    slots.sort(key=lambda slot: slot.publish_at)
    return slots


def _next_window_occurrence(after: datetime, weekday: int, hour: int) -> datetime:
    """The first ``weekday`` at ``hour`` UTC strictly after ``after``."""
    candidate = after.replace(hour=hour, minute=0, second=0, microsecond=0)
    days_ahead = (weekday - candidate.weekday()) % 7
    candidate += timedelta(days=days_ahead)
    if candidate <= after:
        candidate += timedelta(days=7)
    return candidate


def heuristic_strategy(
    analytics: Optional[AnalyticsSummary], goals: UserGoals, platforms: Sequence[Platform]
) -> StrategyDraft:
    """Plan straight from the measurements: best format, proven topics."""
    clusters: list[DraftTopicCluster] = []
    recommendations: list[DraftRecommendation] = []

    winning = list((analytics.winning_topics if analytics else []) or [])
    themes = winning or ["what our customers actually ask us", "how the work gets done"]
    for theme in themes[:3]:
        clusters.append(
            DraftTopicCluster(
                name=theme[:60],
                rationale="Derived from the account's own top-performing posts.",
                keywords=list(theme.lower().split()[:5]),
                platforms=[p.value for p in platforms],
            )
        )

    for platform in platforms:
        stats = analytics.per_platform.get(platform.value) if analytics else None
        if stats and stats.format_performance:
            best_format = max(stats.format_performance.items(), key=lambda kv: kv[1])[0]
        else:
            best_format = rules_for(platform).supported_formats[0].value
        for index in range(goals.posts_per_platform):
            theme = themes[index % len(themes)]
            recommendations.append(
                DraftRecommendation(
                    title=f"{theme[:70]} ({platform.value} #{index + 1})",
                    angle="Lead with the outcome, then show the process.",
                    platform=platform.value,
                    content_format=best_format,
                    topic_cluster=clusters[index % len(clusters)].name,
                    priority=2 if index == 0 else 3,
                    reasoning=(
                        f"{best_format} is the strongest measured format on "
                        f"{platform.value}."
                    ),
                )
            )

    return StrategyDraft(
        objective=goals.objective,
        notes="Generated without an LLM from measured performance.",
        topic_clusters=clusters,
        recommendations=recommendations,
    )


def _render_goals(goals: UserGoals) -> str:
    lines = [
        f"Objective: {goals.objective}",
        f"Horizon: {goals.horizon_days} days",
        f"Posts per platform: {goals.posts_per_platform}",
        "Platforms: " + ", ".join(p.value for p in goals.target_platforms),
    ]
    if goals.tone:
        lines.append(f"Requested tone: {goals.tone}")
    if goals.must_include:
        lines.append("Must include: " + "; ".join(goals.must_include))
    if goals.must_avoid:
        lines.append("Must avoid: " + "; ".join(goals.must_avoid))
    return "\n".join(lines)


def _render_analytics(analytics: Optional[AnalyticsSummary]) -> str:
    if analytics is None:
        return "No analytics available."
    lines = [analytics.narrative or "", ""]
    lines += [f"Strength: {item}" for item in analytics.highlights]
    lines += [f"Gap: {item}" for item in analytics.weaknesses]
    lines += [f"Working topic: {item}" for item in analytics.winning_topics]
    lines += [f"Weak topic: {item}" for item in analytics.underperforming_topics]
    for name, platform in analytics.per_platform.items():
        supported = ", ".join(f.value for f in rules_for(platform.platform).supported_formats)
        formats = ", ".join(
            f"{fmt}={rate:.4f}" for fmt, rate in platform.format_performance.items()
        )
        lines.append(
            f"{name}: {platform.posts_analyzed} posts, engagement by format [{formats}]"
            f"; supported formats: {supported}"
        )
    return "\n".join(line for line in lines if line is not None)


@agent_node(NodeName.STRATEGY, ExecutionStatus.STRATEGIZING)
async def strategy_node(state: AgentState) -> dict[str, Any]:
    goals: UserGoals = state.get("goals") or UserGoals()
    analytics: Optional[AnalyticsSummary] = state.get("raw_analytics")
    platforms = active_platforms(state)

    llm = get_llm_client()
    request = LLMRequest(
        intent="strategy.plan",
        system=STRATEGY_SYSTEM,
        prompt=(
            "ACCOUNT GOALS\n"
            f"{_render_goals(goals)}\n\n"
            "PERFORMANCE SUMMARY\n"
            f"{_render_analytics(analytics)}\n\n"
            f"Produce exactly {goals.posts_per_platform} recommendation(s) per "
            "platform listed above, and 2-3 topic clusters. Use only the "
            "platform names and content formats shown."
        ),
        fallback=lambda: heuristic_strategy(analytics, goals, platforms),
    )
    draft = await llm.parse(request, StrategyDraft)

    clusters: list[TopicCluster] = []
    for item in draft.topic_clusters:
        targets = [
            platform
            for platform in (_coerce_platform(value, platforms) for value in item.platforms)
            if platform is not None
        ]
        clusters.append(
            TopicCluster(
                name=item.name.strip()[:120],
                rationale=item.rationale,
                keywords=item.keywords[:12],
                target_platforms=targets or list(platforms),
                expected_lift=item.expected_lift,
            )
        )

    recommendations: list[ContentRecommendation] = []
    per_platform_count: dict[Platform, int] = {}
    for item in draft.recommendations:
        platform = _coerce_platform(item.platform, platforms)
        if platform is None:
            log.info("strategy: dropping recommendation for %r", item.platform)
            continue
        used = per_platform_count.get(platform, 0)
        if used >= goals.posts_per_platform:
            continue  # The LLM over-produced; the goal caps the run.
        per_platform_count[platform] = used + 1
        recommendations.append(
            ContentRecommendation(
                title=item.title.strip()[:200],
                angle=item.angle,
                platform=platform,
                content_format=_coerce_format(item.content_format, platform),
                topic_cluster=item.topic_cluster,
                priority=min(max(item.priority, 1), 5),
                reasoning=item.reasoning,
            )
        )

    if not recommendations:
        # Never hand the content agent an empty plan.
        fallback = heuristic_strategy(analytics, goals, platforms)
        for item in fallback.recommendations:
            platform = _coerce_platform(item.platform, platforms)
            if platform is None:
                continue
            recommendations.append(
                ContentRecommendation(
                    title=item.title,
                    angle=item.angle,
                    platform=platform,
                    content_format=_coerce_format(item.content_format, platform),
                    topic_cluster=item.topic_cluster,
                    priority=item.priority,
                    reasoning=item.reasoning,
                )
            )

    recommendations.sort(key=lambda rec: (rec.priority, rec.platform.value))
    plan = StrategyPlan(
        objective=draft.objective or goals.objective,
        horizon_days=goals.horizon_days,
        topic_clusters=clusters,
        recommendations=recommendations,
        schedule=build_schedule(recommendations, analytics, goals),
        kpis=_target_kpis(analytics),
        notes=draft.notes,
    )
    return {
        "strategy_plan": plan,
        "next_node": NodeName.CONTENT_CREATOR,
        "_detail": f"{len(recommendations)} recommendation(s), "
        f"{len(clusters)} cluster(s)",
    }


def _target_kpis(analytics: Optional[AnalyticsSummary]) -> dict[str, float]:
    """Modest, measurable targets anchored on the current baseline."""
    if analytics is None:
        return {}
    rates = [
        stats.avg_engagement_rate
        for stats in analytics.per_platform.values()
        if stats.avg_engagement_rate is not None
    ]
    if not rates:
        return {}
    baseline = sum(rates) / len(rates)
    return {
        "baseline_engagement_rate": round(baseline, 6),
        "target_engagement_rate": round(baseline * 1.15, 6),
    }
