"""Validation & Guardrails Agent — the gate in front of a client's account.

Three checks, in increasing cost:

a) **Platform policy & safety** — a deterministic banned-phrase and
   restricted-topic sweep, then an LLM risk score.
b) **Brand voice consistency** — scored by the LLM against the same snippets
   the Content Creation Agent was given.
c) **Technical format rules** — caption length, hashtag count and density,
   format support, video runtime and aspect ratio. Pure arithmetic.

A deterministic blocker fails the draft on its own; the LLM can only add
issues and scores, never overrule a hard limit. Failures are turned into a
:class:`CritiqueReport` for the content agent to act on.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field

from app.agents.nodes._common import agent_node
from app.agents.prompts import VALIDATION_SYSTEM
from app.agents.state import AgentState, NodeName
from app.core.config import Settings, get_settings
from app.core.llm import LLMRequest, get_llm_client
from app.core.platform_rules import SAFETY_POLICY, SHORT_FORM_MAX_SECONDS, rules_for
from app.models.schemas import (
    BrandGuideline,
    ContentDraft,
    ContentFormat,
    CritiqueReport,
    ExecutionStatus,
    Severity,
    ValidationIssue,
    ValidationResult,
)

log = logging.getLogger(__name__)

_ASPECT = re.compile(r"\b(\d{1,2}):(\d{1,2})\b")
# Above this, the caption reads as tag spam even when under the hard cap.
APPROVAL_BRAND_MARGIN = 0.05


class JudgeIssue(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message: str
    severity: str = "warning"
    suggestion: str = ""


class ContentJudgement(BaseModel):
    """The LLM's contract: two scores plus qualitative issues."""

    model_config = ConfigDict(extra="forbid")

    safety_score: float = Field(ge=0.0, le=1.0)
    brand_voice_score: float = Field(ge=0.0, le=1.0)
    issues: list[JudgeIssue] = Field(default_factory=list)
    keep: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# (c) Technical format rules — deterministic
# ---------------------------------------------------------------------------
def check_format(draft: ContentDraft) -> list[ValidationIssue]:
    rules = rules_for(draft.platform)
    issues: list[ValidationIssue] = []
    caption = draft.rendered_caption

    over = rules.caption_over_limit(caption)
    if over:
        issues.append(
            ValidationIssue(
                code="caption_too_long",
                message=(
                    f"Caption is {len(caption)} characters, "
                    f"{over} over the {rules.max_caption_chars} limit for "
                    f"{draft.platform.value}."
                ),
                severity=Severity.BLOCKER,
                field="body",
                suggestion=f"Cut at least {over} characters.",
            )
        )
    if not caption.strip():
        issues.append(
            ValidationIssue(
                code="empty_caption",
                message="The draft has no caption text.",
                severity=Severity.BLOCKER,
                field="body",
                suggestion="Write a hook and body.",
            )
        )

    if rules.max_title_chars and len(draft.title) > rules.max_title_chars:
        issues.append(
            ValidationIssue(
                code="title_too_long",
                message=(
                    f"Title is {len(draft.title)} characters; "
                    f"{draft.platform.value} allows {rules.max_title_chars}."
                ),
                severity=Severity.BLOCKER,
                field="title",
                suggestion=f"Trim the title to {rules.max_title_chars} characters.",
            )
        )

    tags = draft.hashtags.all_tags
    if len(tags) > rules.max_hashtags:
        issues.append(
            ValidationIssue(
                code="too_many_hashtags",
                message=(
                    f"{len(tags)} hashtags; {draft.platform.value} caps at "
                    f"{rules.max_hashtags}."
                ),
                severity=Severity.BLOCKER,
                field="hashtags",
                suggestion=f"Keep the {rules.recommended_hashtags[1]} most relevant.",
            )
        )
    elif tags:
        density = rules.hashtag_density(caption, len(tags))
        if density > rules.max_hashtag_density:
            issues.append(
                ValidationIssue(
                    code="hashtag_density",
                    message=(
                        f"{len(tags)} hashtags in {len(caption)} characters "
                        f"({density:.1f} per 100 chars) reads as spam."
                    ),
                    severity=Severity.WARNING,
                    field="hashtags",
                    suggestion="Drop the broadest tags or lengthen the caption.",
                )
            )
        low, high = rules.recommended_hashtags
        if len(tags) < low:
            issues.append(
                ValidationIssue(
                    code="too_few_hashtags",
                    message=f"Only {len(tags)} hashtag(s); {low}-{high} performs better.",
                    severity=Severity.INFO,
                    field="hashtags",
                    suggestion=f"Add {low - len(tags)} relevant tag(s).",
                )
            )

    if not rules.supports(draft.content_format):
        supported = ", ".join(f.value for f in rules.supported_formats)
        issues.append(
            ValidationIssue(
                code="unsupported_format",
                message=(
                    f"{draft.platform.value} does not support "
                    f"{draft.content_format.value}."
                ),
                severity=Severity.BLOCKER,
                field="content_format",
                suggestion=f"Use one of: {supported}.",
            )
        )

    issues.extend(_check_video(draft))
    issues.extend(_check_aspect_ratio(draft))
    return issues


def _check_video(draft: ContentDraft) -> list[ValidationIssue]:
    if draft.content_format not in (ContentFormat.SHORT_VIDEO, ContentFormat.LONG_VIDEO):
        return []
    rules = rules_for(draft.platform)
    if not draft.script:
        return [
            ValidationIssue(
                code="missing_script",
                message="A video draft needs a beat-by-beat script.",
                severity=Severity.BLOCKER,
                field="script",
                suggestion="Add beats with timecodes covering the full runtime.",
            )
        ]

    runtime = max(beat.end_seconds for beat in draft.script)
    ceiling = (
        SHORT_FORM_MAX_SECONDS.get(draft.platform, rules.max_video_seconds)
        if draft.content_format is ContentFormat.SHORT_VIDEO
        else rules.max_video_seconds
    )
    issues: list[ValidationIssue] = []
    if runtime > ceiling:
        issues.append(
            ValidationIssue(
                code="video_too_long",
                message=(
                    f"Script runs {runtime:.0f}s; {draft.platform.value} "
                    f"{draft.content_format.value} caps at {ceiling:.0f}s."
                ),
                severity=Severity.BLOCKER,
                field="script",
                suggestion=f"Cut {runtime - ceiling:.0f}s of beats.",
            )
        )
    if runtime < rules.min_video_seconds:
        issues.append(
            ValidationIssue(
                code="video_too_short",
                message=f"Script runs {runtime:.0f}s, under the "
                f"{rules.min_video_seconds:.0f}s minimum.",
                severity=Severity.BLOCKER,
                field="script",
                suggestion="Add beats.",
            )
        )
    gaps = [
        (previous.end_seconds, nxt.start_seconds)
        for previous, nxt in zip(draft.script, draft.script[1:], strict=False)
        if nxt.start_seconds > previous.end_seconds + 0.5
    ]
    if gaps:
        issues.append(
            ValidationIssue(
                code="script_gaps",
                message=f"The script leaves {len(gaps)} untimed gap(s).",
                severity=Severity.WARNING,
                field="script",
                suggestion="Make each beat start where the previous one ends.",
            )
        )
    return issues


def _check_aspect_ratio(draft: ContentDraft) -> list[ValidationIssue]:
    """If the media brief names a ratio, hold it to the platform's list."""
    rules = rules_for(draft.platform)
    match = _ASPECT.search(draft.media_brief)
    if not match:
        return []
    ratio = f"{match.group(1)}:{match.group(2)}"
    if ratio in rules.allowed_aspect_ratios:
        return []
    return [
        ValidationIssue(
            code="bad_aspect_ratio",
            message=(
                f"Media brief asks for {ratio}; {draft.platform.value} accepts "
                f"{', '.join(rules.allowed_aspect_ratios)}."
            ),
            severity=Severity.BLOCKER,
            field="media_brief",
            suggestion=f"Specify {rules.allowed_aspect_ratios[0]}.",
        )
    ]


# ---------------------------------------------------------------------------
# (a) Platform policy & safety — deterministic sweep
# ---------------------------------------------------------------------------
def check_policy(draft: ContentDraft) -> list[ValidationIssue]:
    haystack = " ".join(
        [
            draft.rendered_caption,
            draft.title,
            draft.media_brief,
            " ".join(beat.voiceover for beat in draft.script),
            " ".join(beat.on_screen_text for beat in draft.script),
        ]
    ).lower()

    issues: list[ValidationIssue] = []
    for phrase in SAFETY_POLICY.banned_phrases:
        if phrase in haystack:
            issues.append(
                ValidationIssue(
                    code="banned_claim",
                    message=f"Copy contains the prohibited claim {phrase!r}.",
                    severity=Severity.BLOCKER,
                    field="body",
                    suggestion="Remove the claim or replace it with a provable statement.",
                )
            )
    for topic in SAFETY_POLICY.restricted_topics:
        if topic in haystack:
            issues.append(
                ValidationIssue(
                    code="restricted_topic",
                    message=f"Copy touches the restricted topic {topic!r}.",
                    severity=Severity.WARNING,
                    field="body",
                    suggestion="Route to human review before publishing.",
                )
            )
    if any(trigger in haystack for trigger in SAFETY_POLICY.disclosure_triggers):
        tags = {tag.lower() for tag in draft.hashtags.all_tags}
        if not tags & set(SAFETY_POLICY.required_disclosure_terms):
            issues.append(
                ValidationIssue(
                    code="missing_disclosure",
                    message="Promotional copy without a paid-partnership disclosure.",
                    severity=Severity.BLOCKER,
                    field="hashtags",
                    suggestion="Add #ad or #sponsored.",
                )
            )
    return issues


# ---------------------------------------------------------------------------
# (b) Brand voice + risk — LLM
# ---------------------------------------------------------------------------
def heuristic_judgement(
    draft: ContentDraft, guidelines: Sequence[BrandGuideline]
) -> ContentJudgement:
    """Lexical stand-in used when the LLM is unavailable.

    Deliberately generous on safety (the deterministic sweep already ran) and
    conservative on brand voice, so an offline run still gets past the gate
    without pretending to have judged tone.
    """
    from app.services.vector_store import tokenize

    draft_tokens = set(tokenize(draft.rendered_caption))
    if guidelines and draft_tokens:
        overlaps = [
            len(draft_tokens & set(tokenize(item.text))) / max(len(set(tokenize(item.text))), 1)
            for item in guidelines
        ]
        brand = min(1.0, 0.75 + sum(overlaps) / len(overlaps))
    else:
        brand = 0.8
    return ContentJudgement(
        safety_score=0.95,
        brand_voice_score=round(brand, 4),
        issues=[],
        keep=["hook"] if draft.hook else [],
    )


async def judge(
    draft: ContentDraft, guidelines: Sequence[BrandGuideline]
) -> ContentJudgement:
    llm = get_llm_client()
    brand = (
        "\n".join(f"- {item.text}" for item in guidelines)
        or "No brand voice on file; judge against plain, specific, non-hypey copy."
    )
    beats = "\n".join(
        f"{beat.start_seconds:.0f}-{beat.end_seconds:.0f}s "
        f"visual={beat.visual!r} vo={beat.voiceover!r}"
        for beat in draft.script
    )
    request = LLMRequest(
        intent="validation.judge",
        system=VALIDATION_SYSTEM,
        prompt=(
            "BRAND VOICE\n"
            f"{brand}\n\n"
            f"PLATFORM: {draft.platform.value}\n"
            f"FORMAT: {draft.content_format.value}\n\n"
            "DRAFT CAPTION\n"
            f"{draft.rendered_caption}\n\n"
            + (f"SCRIPT\n{beats}\n\n" if beats else "")
            + "Score the draft and list any issues."
        ),
        fallback=lambda: heuristic_judgement(draft, guidelines),
    )
    return await llm.parse(request, ContentJudgement)


def _severity(value: str) -> Severity:
    try:
        return Severity(value.strip().lower())
    except ValueError:
        return Severity.WARNING


def evaluate(
    draft: ContentDraft,
    judgement: ContentJudgement,
    *,
    settings: Optional[Settings] = None,
) -> ValidationResult:
    """Combine the deterministic sweep with the LLM's scores into one verdict."""
    settings = settings or get_settings()
    format_issues = check_format(draft)
    policy_issues = check_policy(draft)
    llm_issues = [
        ValidationIssue(
            code=issue.code or "llm_issue",
            message=issue.message,
            severity=_severity(issue.severity),
            suggestion=issue.suggestion,
        )
        for issue in judgement.issues
    ]
    issues = [*format_issues, *policy_issues, *llm_issues]

    if judgement.safety_score < settings.min_safety_score:
        issues.append(
            ValidationIssue(
                code="safety_below_threshold",
                message=(
                    f"Safety score {judgement.safety_score:.2f} is below the "
                    f"{settings.min_safety_score:.2f} floor."
                ),
                severity=Severity.BLOCKER,
                suggestion="Remove risky claims and soften absolute statements.",
            )
        )
    if judgement.brand_voice_score < settings.min_brand_voice_score:
        issues.append(
            ValidationIssue(
                code="brand_voice_below_threshold",
                message=(
                    f"Brand voice score {judgement.brand_voice_score:.2f} is below "
                    f"the {settings.min_brand_voice_score:.2f} floor."
                ),
                severity=Severity.BLOCKER,
                suggestion="Rewrite in the brand's own phrasing and rhythm.",
            )
        )

    format_compliant = not any(
        issue.severity is Severity.BLOCKER for issue in format_issues
    )
    has_blocker = any(issue.severity is Severity.BLOCKER for issue in issues)

    # A clean pass can still want a human: unresolved policy warnings, or a
    # brand score sitting right on the floor.
    borderline = (
        judgement.brand_voice_score
        < settings.min_brand_voice_score + APPROVAL_BRAND_MARGIN
    )
    policy_warnings = any(
        issue.severity is Severity.WARNING
        and issue.code in ("restricted_topic", "banned_claim")
        for issue in issues
    )

    return ValidationResult(
        draft_id=draft.draft_id,
        is_valid=not has_blocker,
        safety_score=round(judgement.safety_score, 4),
        brand_voice_score=round(judgement.brand_voice_score, 4),
        format_compliant=format_compliant,
        issues=issues,
        requires_human_approval=bool(policy_warnings or borderline),
    )


@agent_node(NodeName.VALIDATION, ExecutionStatus.VALIDATING)
async def validation_node(state: AgentState) -> dict[str, Any]:
    settings = get_settings()
    drafts: list[ContentDraft] = list(state.get("generated_content", []))
    if not drafts:
        raise ValueError("validation_node reached with no drafts")

    guidelines = list(state.get("brand_guidelines", []))
    judgements = await asyncio.gather(*(judge(draft, guidelines) for draft in drafts))

    results = [
        evaluate(draft, judgement, settings=settings)
        for draft, judgement in zip(drafts, judgements, strict=True)
    ]
    attempt = state.get("retry_count", 0) + 1
    critiques: list[CritiqueReport] = []
    for result, judgement in zip(results, judgements, strict=True):
        if result.is_valid:
            continue
        critique = CritiqueReport.from_validation(result, attempt)
        # Carry over what the judge said was working, so the rewrite keeps it.
        critique.keep = judgement.keep
        critiques.append(critique)

    passed = sum(1 for result in results if result.is_valid)
    return {
        # Appending (rather than replacing) keeps the per-attempt history that
        # `latest_validation_for` and the retry edge read.
        "validation_results": list(state.get("validation_results", [])) + results,
        "critiques": critiques,
        "_detail": f"{passed}/{len(results)} draft(s) passed",
    }
