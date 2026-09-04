"""Guardrails: the deterministic half must fail drafts on its own."""

from __future__ import annotations

from app.agents.nodes.validation import (
    ContentJudgement,
    check_format,
    check_policy,
    evaluate,
)
from app.models.schemas import (
    ContentFormat,
    CritiqueReport,
    HashtagStrategy,
    Platform,
    ScriptBeat,
    Severity,
)

CLEAN = ContentJudgement(safety_score=0.98, brand_voice_score=0.9)


def _codes(issues) -> set[str]:
    return {issue.code for issue in issues}


def test_clean_draft_passes(draft, settings):
    result = evaluate(draft, CLEAN, settings=settings)
    assert result.is_valid is True
    assert result.format_compliant is True
    assert result.blockers == []


def test_caption_over_the_platform_limit_is_a_blocker(draft, settings):
    long_draft = draft.model_copy(update={"body": "x" * 2500})
    issues = check_format(long_draft)

    assert "caption_too_long" in _codes(issues)
    result = evaluate(long_draft, CLEAN, settings=settings)
    assert result.is_valid is False
    assert result.format_compliant is False


def test_hashtag_cap_and_density(draft, settings):
    over_cap = draft.model_copy(
        update={"hashtags": HashtagStrategy(broad=[f"tag{i}" for i in range(35)])}
    )
    assert "too_many_hashtags" in _codes(check_format(over_cap))

    # Under the cap but crammed into a short caption: a warning, not a blocker.
    dense = draft.model_copy(
        update={
            "hook": "Short.",
            "body": "",
            "call_to_action": "",
            "hashtags": HashtagStrategy(broad=[f"tag{i}" for i in range(12)]),
        }
    )
    issues = check_format(dense)
    assert "hashtag_density" in _codes(issues)
    assert evaluate(dense, CLEAN, settings=settings).is_valid is True


def test_youtube_title_limit(draft, settings):
    yt = draft.model_copy(
        update={
            "platform": Platform.YOUTUBE,
            "content_format": ContentFormat.LONG_VIDEO,
            "title": "t" * 120,
            "media_brief": "16:9 talking head",
        }
    )
    assert "title_too_long" in _codes(check_format(yt))


def test_unsupported_format_for_platform(draft):
    text_on_tiktok = draft.model_copy(
        update={"platform": Platform.TIKTOK, "content_format": ContentFormat.TEXT_POST}
    )
    assert "unsupported_format" in _codes(check_format(text_on_tiktok))


def test_short_video_runtime_bounds(draft):
    too_long = draft.model_copy(
        update={
            "script": [ScriptBeat(start_seconds=0, end_seconds=200, visual="one shot")]
        }
    )
    assert "video_too_long" in _codes(check_format(too_long))

    missing = draft.model_copy(update={"script": []})
    assert "missing_script" in _codes(check_format(missing))


def test_script_gaps_are_flagged(draft):
    gapped = draft.model_copy(
        update={
            "script": [
                ScriptBeat(start_seconds=0, end_seconds=4, visual="a"),
                ScriptBeat(start_seconds=10, end_seconds=20, visual="b"),
            ]
        }
    )
    assert "script_gaps" in _codes(check_format(gapped))


def test_aspect_ratio_must_match_the_platform(draft):
    wide_reel = draft.model_copy(update={"media_brief": "Landscape 16:9 footage"})
    assert "bad_aspect_ratio" in _codes(check_format(wide_reel))


def test_banned_claim_blocks_regardless_of_llm_opinion(draft, settings):
    risky = draft.model_copy(
        update={"body": "This is a risk-free investment with guaranteed income."}
    )
    issues = check_policy(risky)
    assert "banned_claim" in _codes(issues)

    # Even a perfect LLM score cannot rescue it.
    result = evaluate(risky, ContentJudgement(safety_score=1.0, brand_voice_score=1.0),
                      settings=settings)
    assert result.is_valid is False


def test_promotional_copy_needs_a_disclosure(draft, settings):
    promo = draft.model_copy(
        update={"body": "This is a paid partnership with our friends at Acme."}
    )
    assert "missing_disclosure" in _codes(check_policy(promo))

    disclosed = promo.model_copy(
        update={"hashtags": HashtagStrategy(broad=["ad"], niche=["onboarding"])}
    )
    assert "missing_disclosure" not in _codes(check_policy(disclosed))


def test_scores_below_the_configured_floors_block(draft, settings):
    low_safety = evaluate(
        draft, ContentJudgement(safety_score=0.4, brand_voice_score=0.95),
        settings=settings,
    )
    assert low_safety.is_valid is False
    assert "safety_below_threshold" in _codes(low_safety.issues)

    low_brand = evaluate(
        draft, ContentJudgement(safety_score=0.99, brand_voice_score=0.2),
        settings=settings,
    )
    assert low_brand.is_valid is False
    assert "brand_voice_below_threshold" in _codes(low_brand.issues)


def test_borderline_brand_score_requests_a_human(draft, settings):
    borderline = evaluate(
        draft,
        ContentJudgement(
            safety_score=0.99,
            brand_voice_score=settings.min_brand_voice_score + 0.01,
        ),
        settings=settings,
    )
    assert borderline.is_valid is True
    assert borderline.requires_human_approval is True


def test_critique_report_splits_blockers_from_nits(draft, settings):
    bad = draft.model_copy(update={"body": "x" * 2500})
    result = evaluate(bad, ContentJudgement(safety_score=0.99, brand_voice_score=0.9),
                      settings=settings)
    critique = CritiqueReport.from_validation(result, attempt=1)

    assert critique.draft_id == bad.draft_id
    assert critique.attempt == 1
    assert any("caption_too_long" in item for item in critique.must_fix)
    assert all(
        issue.severity is not Severity.BLOCKER
        or any(issue.code in item for item in critique.must_fix)
        for issue in result.issues
    )
