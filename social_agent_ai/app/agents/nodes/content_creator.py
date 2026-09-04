"""Content Creation Agent — write the posts, on brand, inside the limits.

Two modes, decided by what is already in the state:

* **First pass** — one draft per recommendation from the strategy plan.
* **Revision pass** — only the drafts whose latest verdict failed, rewritten
  against their :class:`CritiqueReport`. ``draft_id`` is preserved so the
  validation history for a piece stays continuous, ``revision`` increments,
  and ``retry_count`` goes up once per pass (this is the counter the
  validation edge tests against).

Brand voice comes from the vector store, and the same snippets are handed to
the Validation Agent, so "on brand" means one thing across the pipeline.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agents.nodes._common import agent_node
from app.agents.prompts import CONTENT_REVISION_SYSTEM, CONTENT_SYSTEM
from app.agents.state import (
    AgentState,
    NodeName,
    active_platforms,
    failed_drafts,
    latest_validation_for,
)
from app.core.llm import LLMRequest, get_llm_client
from app.core.platform_rules import SHORT_FORM_MAX_SECONDS, PlatformRules, rules_for
from app.models.schemas import (
    BrandGuideline,
    ContentDraft,
    ContentFormat,
    ContentRecommendation,
    CritiqueReport,
    ExecutionStatus,
    HashtagStrategy,
    ScriptBeat,
    StrategyPlan,
    UserGoals,
)
from app.services.vector_store import get_brand_voice_store

log = logging.getLogger(__name__)

BRAND_SNIPPETS = 5


class DraftBeat(BaseModel):
    model_config = ConfigDict(extra="forbid")

    start_seconds: float = 0.0
    end_seconds: float = 0.0
    visual: str = ""
    voiceover: str = ""
    on_screen_text: str = ""


class DraftCopy(BaseModel):
    """The LLM's contract for one piece of content."""

    model_config = ConfigDict(extra="forbid")

    title: str = ""
    hook: str
    body: str
    call_to_action: str = ""
    media_brief: str = ""
    hashtags_broad: list[str] = Field(default_factory=list)
    hashtags_niche: list[str] = Field(default_factory=list)
    hashtags_branded: list[str] = Field(default_factory=list)
    script: list[DraftBeat] = Field(default_factory=list)


def _render_rules(rules: PlatformRules, content_format: ContentFormat) -> str:
    low, high = rules.recommended_hashtags
    lines = [
        f"Platform: {rules.platform.value}",
        f"Format: {content_format.value}",
        (
            f"Caption hard limit: {rules.max_caption_chars} characters "
            "(hook + body + CTA + hashtags combined)"
        ),
        f"Hashtags: {low}-{high} is ideal, {rules.max_hashtags} is the hard cap",
        f"Aspect ratios: {', '.join(rules.allowed_aspect_ratios)}",
    ]
    if rules.max_title_chars:
        lines.append(f"Title limit: {rules.max_title_chars} characters")
    if content_format in (ContentFormat.SHORT_VIDEO, ContentFormat.LONG_VIDEO):
        ceiling = SHORT_FORM_MAX_SECONDS.get(rules.platform, rules.max_video_seconds)
        lines.append(
            f"Video length: {rules.min_video_seconds:.0f}-{ceiling:.0f} seconds; "
            "write beats that cover the whole runtime"
        )
    return "\n".join(lines)


def _render_brand(guidelines: Sequence[BrandGuideline]) -> str:
    if not guidelines:
        return "No brand voice on file. Default to plain, specific, non-hypey copy."
    return "\n".join(f"- {item.text}" for item in guidelines)


def _render_goals(goals: UserGoals) -> str:
    lines = [f"Objective: {goals.objective}"]
    if goals.tone:
        lines.append(f"Tone: {goals.tone}")
    if goals.must_include:
        lines.append("Must mention: " + "; ".join(goals.must_include))
    if goals.must_avoid:
        lines.append("Never mention: " + "; ".join(goals.must_avoid))
    return "\n".join(lines)


def _render_critique(critique: CritiqueReport) -> str:
    lines = [f"This is revision attempt {critique.attempt}."]
    if critique.must_fix:
        lines.append("MUST FIX:")
        lines += [f"- {item}" for item in critique.must_fix]
    if critique.should_fix:
        lines.append("SHOULD FIX:")
        lines += [f"- {item}" for item in critique.should_fix]
    if critique.keep:
        lines.append("KEEP:")
        lines += [f"- {item}" for item in critique.keep]
    if critique.revision_instructions:
        lines.append(critique.revision_instructions)
    return "\n".join(lines)


def heuristic_copy(
    recommendation: ContentRecommendation,
    rules: PlatformRules,
    guidelines: Sequence[BrandGuideline],
) -> DraftCopy:
    """A safe, limit-respecting draft used when the LLM is unavailable.

    It is intentionally plain: it exists so the pipeline stays exercisable
    offline, not to compete with the model's writing.
    """
    topic = recommendation.topic_cluster or recommendation.title
    hook = f"{recommendation.title.strip()}"[:120]
    body_parts = [
        recommendation.angle or "Here is the short version, then the detail.",
        f"Why it matters: {recommendation.reasoning}" if recommendation.reasoning else "",
    ]
    if guidelines:
        body_parts.append(f"Voice note applied: {guidelines[0].text}")
    body = "\n\n".join(part for part in body_parts if part)

    low, _ = rules.recommended_hashtags
    tag_seed = [word for word in topic.lower().split() if len(word) > 3][: max(low, 2)]
    script: list[DraftBeat] = []
    if recommendation.content_format is ContentFormat.SHORT_VIDEO:
        ceiling = min(SHORT_FORM_MAX_SECONDS.get(rules.platform, 30), 30)
        step = ceiling / 3
        script = [
            DraftBeat(
                start_seconds=round(index * step, 1),
                end_seconds=round((index + 1) * step, 1),
                visual=beat_visual,
                voiceover=beat_voice,
                on_screen_text=beat_text,
            )
            for index, (beat_visual, beat_voice, beat_text) in enumerate(
                (
                    ("Open on the result", hook, hook[:40]),
                    ("Show the process", recommendation.angle or topic, "How"),
                    ("Close on the invitation", "Full breakdown in the caption.", "More"),
                )
            )
        ]

    return DraftCopy(
        title=recommendation.title[: rules.max_title_chars or 100],
        hook=hook,
        body=body[: max(rules.max_caption_chars - 200, 200)],
        call_to_action="Tell us which part you want next.",
        media_brief=f"{recommendation.content_format.value} for {rules.platform.value}: "
        f"{recommendation.angle or topic}",
        hashtags_broad=tag_seed[:1],
        hashtags_niche=tag_seed[1:],
        script=script,
    )


def _to_draft(
    copy: DraftCopy,
    recommendation: ContentRecommendation,
    rules: PlatformRules,
    guidelines: Sequence[BrandGuideline],
    *,
    draft_id: Optional[str] = None,
    revision: int = 0,
    scheduled_for: Optional[Any] = None,
) -> ContentDraft:
    hashtags = HashtagStrategy(
        broad=copy.hashtags_broad,
        niche=copy.hashtags_niche,
        branded=copy.hashtags_branded,
    )
    payload: dict[str, Any] = {
        "platform": recommendation.platform,
        "content_format": recommendation.content_format,
        "recommendation_id": recommendation.recommendation_id,
        "topic_cluster": recommendation.topic_cluster,
        "title": copy.title[: rules.max_title_chars] if rules.max_title_chars else copy.title,
        "hook": copy.hook,
        "body": copy.body,
        "script": [ScriptBeat(**beat.model_dump()) for beat in copy.script],
        "hashtags": hashtags,
        "call_to_action": copy.call_to_action,
        "media_brief": copy.media_brief,
        "scheduled_for": scheduled_for,
        "revision": revision,
        "brand_sources": [item.doc_id for item in guidelines],
    }
    if draft_id:
        payload["draft_id"] = draft_id
    return ContentDraft(**payload)


async def _generate_one(
    recommendation: ContentRecommendation,
    goals: UserGoals,
    guidelines: Sequence[BrandGuideline],
    *,
    critique: Optional[CritiqueReport] = None,
    previous: Optional[ContentDraft] = None,
    scheduled_for: Optional[Any] = None,
) -> ContentDraft:
    rules = rules_for(recommendation.platform)
    llm = get_llm_client()

    sections = [
        "BRAND VOICE (authoritative)",
        _render_brand(guidelines),
        "",
        "ACCOUNT GOALS",
        _render_goals(goals),
        "",
        "PLATFORM BRIEF",
        _render_rules(rules, recommendation.content_format),
        "",
        "WHAT TO WRITE",
        f"Title/idea: {recommendation.title}",
        f"Angle: {recommendation.angle}",
        f"Topic cluster: {recommendation.topic_cluster}",
        f"Why this piece: {recommendation.reasoning}",
    ]
    if previous is not None and critique is not None:
        sections += [
            "",
            "PREVIOUS DRAFT (failed validation)",
            previous.rendered_caption,
            "",
            "CRITIQUE",
            _render_critique(critique),
        ]

    request = LLMRequest(
        intent="content.revise" if critique else "content.create",
        system=CONTENT_REVISION_SYSTEM if critique else CONTENT_SYSTEM,
        prompt="\n".join(sections),
        fallback=lambda: heuristic_copy(recommendation, rules, guidelines),
    )
    copy = await llm.parse(request, DraftCopy)
    return _to_draft(
        copy,
        recommendation,
        rules,
        guidelines,
        draft_id=previous.draft_id if previous else None,
        revision=(previous.revision + 1) if previous else 0,
        scheduled_for=scheduled_for
        or (previous.scheduled_for if previous else None),
    )


@agent_node(NodeName.CONTENT_CREATOR, ExecutionStatus.CREATING)
async def content_creator_node(state: AgentState) -> dict[str, Any]:
    goals: UserGoals = state.get("goals") or UserGoals()
    plan: Optional[StrategyPlan] = state.get("strategy_plan")
    if plan is None or not plan.recommendations:
        raise ValueError("content_creator_node reached without a strategy plan")

    guidelines = await _brand_voice(state, plan)
    schedule = {
        slot.recommendation_id: slot.publish_at
        for slot in plan.schedule
        if slot.recommendation_id
    }
    by_id = {rec.recommendation_id: rec for rec in plan.recommendations}
    to_revise = failed_drafts(state)

    if to_revise:
        return await _revise(state, goals, guidelines, by_id, to_revise, schedule)

    drafts = await asyncio.gather(
        *(
            _generate_one(
                recommendation,
                goals,
                guidelines,
                scheduled_for=schedule.get(recommendation.recommendation_id),
            )
            for recommendation in plan.recommendations
        )
    )
    return {
        "generated_content": list(drafts),
        "brand_guidelines": list(guidelines),
        "next_node": NodeName.VALIDATION,
        "_detail": f"{len(drafts)} draft(s) created",
    }


async def _revise(
    state: AgentState,
    goals: UserGoals,
    guidelines: Sequence[BrandGuideline],
    by_id: dict[str, ContentRecommendation],
    to_revise: Sequence[ContentDraft],
    schedule: dict[str, Any],
) -> dict[str, Any]:
    """Rewrite only the failed drafts; keep the passing ones untouched."""
    critiques = {c.draft_id: c for c in state.get("critiques", [])}
    attempt = state.get("retry_count", 0) + 1
    targets: list[tuple[ContentDraft, ContentRecommendation, CritiqueReport]] = []

    for draft in to_revise:
        recommendation = by_id.get(draft.recommendation_id or "")
        if recommendation is None:
            # The plan changed under us; reconstruct enough to re-ask.
            recommendation = ContentRecommendation(
                title=draft.title or draft.hook,
                platform=draft.platform,
                content_format=draft.content_format,
                topic_cluster=draft.topic_cluster,
            )
        critique = critiques.get(draft.draft_id)
        if critique is None:
            verdict = latest_validation_for(state, draft.draft_id)
            critique = (
                CritiqueReport.from_validation(verdict, attempt)
                if verdict is not None
                else CritiqueReport(draft_id=draft.draft_id, attempt=attempt)
            )
        targets.append((draft, recommendation, critique))

    revised = await asyncio.gather(
        *(
            _generate_one(
                recommendation,
                goals,
                guidelines,
                critique=critique,
                previous=draft,
                scheduled_for=schedule.get(recommendation.recommendation_id),
            )
            for draft, recommendation, critique in targets
        )
    )
    replacements = {draft.draft_id: draft for draft in revised}
    merged = [
        replacements.get(draft.draft_id, draft)
        for draft in state.get("generated_content", [])
    ]
    return {
        "generated_content": merged,
        "brand_guidelines": list(guidelines),
        "retry_count": attempt,
        "next_node": NodeName.VALIDATION,
        "_detail": f"revision {attempt}: {len(revised)} draft(s) rewritten",
    }


async def _brand_voice(
    state: AgentState, plan: StrategyPlan
) -> list[BrandGuideline]:
    """Retrieve tone-of-voice snippets, reusing what an earlier pass fetched."""
    existing = state.get("brand_guidelines") or []
    if existing:
        return list(existing)

    store = get_brand_voice_store()
    query_parts = [plan.objective] + [cluster.name for cluster in plan.topic_clusters]
    query_parts += [rec.title for rec in plan.recommendations[:3]]
    query = " ".join(part for part in query_parts if part)
    platforms = ", ".join(p.value for p in active_platforms(state))
    try:
        return await store.search(
            state["user_id"], f"{query} {platforms}".strip(), limit=BRAND_SNIPPETS
        )
    except Exception as exc:  # noqa: BLE001 - retrieval is best-effort
        log.warning("brand voice retrieval failed: %s", exc)
        return []
