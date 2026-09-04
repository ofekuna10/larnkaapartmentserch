"""Hard, mechanical publishing constraints per platform.

These are the rules the Validation Agent checks deterministically — no LLM
involved — and the same table the Content Creation Agent is prompted with so
it aims inside the box in the first place. Values are conservative: they track
the documented limits but stay below them where a platform has been known to
truncate.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models.schemas import ContentFormat, Platform


@dataclass(frozen=True)
class PlatformRules:
    platform: Platform
    max_caption_chars: int
    max_title_chars: int
    max_hashtags: int
    recommended_hashtags: tuple[int, int]
    supported_formats: tuple[ContentFormat, ...]
    min_video_seconds: float = 0.0
    max_video_seconds: float = 0.0
    allowed_aspect_ratios: tuple[str, ...] = ("9:16",)
    # Hashtags per 100 characters of caption; above this reads as spam.
    max_hashtag_density: float = 3.0

    def caption_over_limit(self, caption: str) -> int:
        """Characters to cut, or 0 when the caption fits."""
        return max(0, len(caption) - self.max_caption_chars)

    def hashtag_density(self, caption: str, hashtag_count: int) -> float:
        body_length = max(len(caption), 1)
        return hashtag_count / (body_length / 100)

    def supports(self, content_format: ContentFormat) -> bool:
        return content_format in self.supported_formats


PLATFORM_RULES: dict[Platform, PlatformRules] = {
    Platform.INSTAGRAM: PlatformRules(
        platform=Platform.INSTAGRAM,
        max_caption_chars=2200,
        max_title_chars=0,  # Instagram has no separate title field.
        max_hashtags=30,
        recommended_hashtags=(3, 12),
        supported_formats=(
            ContentFormat.SHORT_VIDEO,
            ContentFormat.IMAGE_POST,
            ContentFormat.CAROUSEL,
            ContentFormat.STORY,
        ),
        min_video_seconds=3,
        max_video_seconds=90,
        allowed_aspect_ratios=("9:16", "4:5", "1:1"),
    ),
    Platform.FACEBOOK: PlatformRules(
        platform=Platform.FACEBOOK,
        max_caption_chars=5000,
        max_title_chars=255,
        max_hashtags=10,
        recommended_hashtags=(1, 3),
        supported_formats=(
            ContentFormat.SHORT_VIDEO,
            ContentFormat.LONG_VIDEO,
            ContentFormat.IMAGE_POST,
            ContentFormat.CAROUSEL,
            ContentFormat.TEXT_POST,
            ContentFormat.STORY,
        ),
        min_video_seconds=1,
        max_video_seconds=3600,
        allowed_aspect_ratios=("9:16", "1:1", "16:9"),
        max_hashtag_density=1.0,
    ),
    Platform.TIKTOK: PlatformRules(
        platform=Platform.TIKTOK,
        max_caption_chars=2200,
        max_title_chars=0,
        max_hashtags=20,
        recommended_hashtags=(3, 8),
        supported_formats=(ContentFormat.SHORT_VIDEO, ContentFormat.IMAGE_POST),
        min_video_seconds=3,
        max_video_seconds=600,
        allowed_aspect_ratios=("9:16",),
    ),
    Platform.YOUTUBE: PlatformRules(
        platform=Platform.YOUTUBE,
        max_caption_chars=5000,      # description
        max_title_chars=100,
        max_hashtags=15,
        recommended_hashtags=(2, 5),
        supported_formats=(
            ContentFormat.SHORT_VIDEO,
            ContentFormat.LONG_VIDEO,
            ContentFormat.IMAGE_POST,  # community post
        ),
        min_video_seconds=1,
        max_video_seconds=43200,
        allowed_aspect_ratios=("16:9", "9:16"),
    ),
}


# Duration ceiling for the vertical short-form surface of each platform.
SHORT_FORM_MAX_SECONDS: dict[Platform, float] = {
    Platform.INSTAGRAM: 90,
    Platform.TIKTOK: 600,
    Platform.YOUTUBE: 60,
    Platform.FACEBOOK: 90,
}


@dataclass(frozen=True)
class SafetyPolicy:
    """Terms that block a draft outright, regardless of the LLM's opinion."""

    banned_phrases: tuple[str, ...] = (
        "guaranteed income",
        "get rich quick",
        "miracle cure",
        "risk-free investment",
        "cures cancer",
        "double your money",
        "click here to claim your prize",
    )
    restricted_topics: tuple[str, ...] = (
        "prescription drugs",
        "firearms",
        "gambling",
        "tobacco",
        "cryptocurrency giveaway",
    )
    # Claims that need a disclosure rather than a rewrite.
    disclosure_triggers: tuple[str, ...] = ("#ad", "sponsored", "paid partnership")
    required_disclosure_terms: tuple[str, ...] = field(
        default=("ad", "sponsored", "paidpartnership")
    )


SAFETY_POLICY = SafetyPolicy()


def rules_for(platform: Platform) -> PlatformRules:
    try:
        return PLATFORM_RULES[platform]
    except KeyError as exc:  # pragma: no cover - guarded by the Platform enum
        raise ValueError(f"No publishing rules configured for {platform}") from exc
