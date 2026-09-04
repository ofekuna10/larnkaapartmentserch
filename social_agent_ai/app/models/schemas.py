"""Domain schemas shared by the agents, the services and the API layer.

Everything that travels through the LangGraph pipeline is modelled here so a
node never has to guess the shape of what an upstream node produced.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, computed_field, field_validator


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


class Platform(str, Enum):
    """Social networks the platform can read from and publish to."""

    YOUTUBE = "youtube"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TIKTOK = "tiktok"


class ContentFormat(str, Enum):
    SHORT_VIDEO = "short_video"        # Reels / Shorts / TikTok
    LONG_VIDEO = "long_video"          # YouTube long form
    IMAGE_POST = "image_post"
    CAROUSEL = "carousel"
    TEXT_POST = "text_post"
    STORY = "story"


class ExecutionStatus(str, Enum):
    """Coarse pipeline status, surfaced to the API and the dashboard."""

    PENDING = "pending"
    RUNNING = "running"
    ANALYZING = "analyzing"
    STRATEGIZING = "strategizing"
    CREATING = "creating"
    VALIDATING = "validating"
    REVISING = "revising"
    AWAITING_APPROVAL = "awaiting_approval"
    PUBLISHING = "publishing"
    COMPLETED = "completed"
    FAILED = "failed"


class Severity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    BLOCKER = "blocker"


class PublishStatus(str, Enum):
    PUBLISHED = "published"
    SCHEDULED = "scheduled"
    QUEUED_FOR_APPROVAL = "queued_for_approval"
    SKIPPED = "skipped"
    FAILED = "failed"


class SocialBase(BaseModel):
    """Strict base: reject unknown keys so LLM output drift fails loudly."""

    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Connected accounts
# ---------------------------------------------------------------------------
class PlatformConnection(SocialBase):
    platform: Platform
    account_id: str
    handle: str = ""
    scopes: list[str] = Field(default_factory=list)
    token_expires_at: Optional[datetime] = None
    is_active: bool = True

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_expired(self) -> bool:
        if self.token_expires_at is None:
            return False
        return self.token_expires_at <= utcnow()


# ---------------------------------------------------------------------------
# Analytics Agent output
# ---------------------------------------------------------------------------
class PostMetrics(SocialBase):
    """Normalised metrics for one published post, across all platforms."""

    post_id: str
    platform: Platform
    published_at: Optional[datetime] = None
    content_format: Optional[ContentFormat] = None
    title: str = ""
    impressions: int = 0
    reach: int = 0
    views: int = 0
    likes: int = 0
    comments: int = 0
    shares: int = 0
    saves: int = 0
    follows: int = 0
    avg_view_duration_seconds: Optional[float] = None
    duration_seconds: Optional[float] = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def engagement_rate(self) -> Optional[float]:
        """Interactions per impression (falls back to reach, then views)."""
        denominator = self.impressions or self.reach or self.views
        if not denominator:
            return None
        interactions = self.likes + self.comments + self.shares + self.saves
        return round(interactions / denominator, 6)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def retention_rate(self) -> Optional[float]:
        if not self.duration_seconds or self.avg_view_duration_seconds is None:
            return None
        ratio = self.avg_view_duration_seconds / self.duration_seconds
        return round(min(ratio, 1.0), 6)


class PostingWindow(SocialBase):
    """A recurring time slot that historically over-performs."""

    weekday: int = Field(ge=0, le=6, description="0 = Monday")
    hour_utc: int = Field(ge=0, le=23)
    avg_engagement_rate: float = 0.0
    sample_size: int = 0


class PlatformAnalytics(SocialBase):
    platform: Platform
    posts_analyzed: int = 0
    total_views: int = 0
    total_impressions: int = 0
    avg_engagement_rate: Optional[float] = None
    median_engagement_rate: Optional[float] = None
    avg_retention_rate: Optional[float] = None
    follower_growth: Optional[int] = None
    best_windows: list[PostingWindow] = Field(default_factory=list)
    top_posts: list[PostMetrics] = Field(default_factory=list)
    worst_posts: list[PostMetrics] = Field(default_factory=list)
    format_performance: dict[str, float] = Field(
        default_factory=dict, description="content_format -> avg engagement rate"
    )


class AnalyticsSummary(SocialBase):
    """The structured JSON performance summary the Analytics Agent emits."""

    generated_at: datetime = Field(default_factory=utcnow)
    lookback_days: int = 90
    per_platform: dict[str, PlatformAnalytics] = Field(default_factory=dict)
    highlights: list[str] = Field(default_factory=list)
    weaknesses: list[str] = Field(default_factory=list)
    winning_topics: list[str] = Field(default_factory=list)
    underperforming_topics: list[str] = Field(default_factory=list)
    narrative: str = ""

    @computed_field  # type: ignore[prop-decorator]
    @property
    def platforms_covered(self) -> list[str]:
        return sorted(self.per_platform)


# ---------------------------------------------------------------------------
# Strategy Agent output
# ---------------------------------------------------------------------------
class TopicCluster(SocialBase):
    name: str
    rationale: str = ""
    keywords: list[str] = Field(default_factory=list)
    target_platforms: list[Platform] = Field(default_factory=list)
    expected_lift: Optional[float] = Field(
        default=None, description="Engagement lift vs. baseline; 0.15 = +15%"
    )


class ContentRecommendation(SocialBase):
    recommendation_id: str = Field(default_factory=lambda: new_id("rec"))
    title: str
    angle: str = ""
    platform: Platform
    content_format: ContentFormat
    topic_cluster: str = ""
    priority: int = Field(default=3, ge=1, le=5, description="1 = highest")
    reasoning: str = ""


class ScheduleSlot(SocialBase):
    platform: Platform
    publish_at: datetime
    recommendation_id: Optional[str] = None
    content_format: Optional[ContentFormat] = None


class StrategyPlan(SocialBase):
    generated_at: datetime = Field(default_factory=utcnow)
    objective: str = ""
    horizon_days: int = 14
    topic_clusters: list[TopicCluster] = Field(default_factory=list)
    recommendations: list[ContentRecommendation] = Field(default_factory=list)
    schedule: list[ScheduleSlot] = Field(default_factory=list)
    kpis: dict[str, float] = Field(default_factory=dict)
    notes: str = ""


# ---------------------------------------------------------------------------
# Content Creation Agent output
# ---------------------------------------------------------------------------
class ScriptBeat(SocialBase):
    """One beat of a short-form video script."""

    start_seconds: float = 0.0
    end_seconds: float = 0.0
    visual: str = ""
    voiceover: str = ""
    on_screen_text: str = ""


class HashtagStrategy(SocialBase):
    broad: list[str] = Field(default_factory=list)
    niche: list[str] = Field(default_factory=list)
    branded: list[str] = Field(default_factory=list)

    @field_validator("broad", "niche", "branded", mode="after")
    @classmethod
    def _normalise(cls, tags: list[str]) -> list[str]:
        cleaned: list[str] = []
        for tag in tags:
            tag = tag.strip().lstrip("#")
            if tag and tag not in cleaned:
                cleaned.append(tag)
        return cleaned

    @computed_field  # type: ignore[prop-decorator]
    @property
    def all_tags(self) -> list[str]:
        return [*self.broad, *self.niche, *self.branded]


class ContentDraft(SocialBase):
    """A single platform-ready piece of content."""

    draft_id: str = Field(default_factory=lambda: new_id("draft"))
    platform: Platform
    content_format: ContentFormat
    recommendation_id: Optional[str] = None
    topic_cluster: str = ""
    title: str = ""
    hook: str = ""
    body: str = Field(default="", description="Caption / description / post copy")
    script: list[ScriptBeat] = Field(default_factory=list)
    hashtags: HashtagStrategy = Field(default_factory=HashtagStrategy)
    call_to_action: str = ""
    media_brief: str = ""
    media_asset_url: Optional[str] = Field(
        default=None,
        description="Publicly reachable rendered asset; required by video platforms",
    )
    scheduled_for: Optional[datetime] = None
    revision: int = Field(default=0, ge=0)
    brand_sources: list[str] = Field(
        default_factory=list, description="Vector-DB doc ids used for tone of voice"
    )

    @computed_field  # type: ignore[prop-decorator]
    @property
    def rendered_caption(self) -> str:
        """What actually lands on the platform: copy + CTA + hashtags."""
        parts = [p for p in (self.hook, self.body, self.call_to_action) if p]
        caption = "\n\n".join(parts)
        if self.hashtags.all_tags:
            tags = " ".join(f"#{t}" for t in self.hashtags.all_tags)
            caption = f"{caption}\n\n{tags}"
        return caption.strip()


# ---------------------------------------------------------------------------
# Validation Agent output
# ---------------------------------------------------------------------------
class ValidationIssue(SocialBase):
    code: str
    message: str
    severity: Severity = Severity.WARNING
    field: Optional[str] = None
    suggestion: str = ""


class ValidationResult(SocialBase):
    draft_id: str
    checked_at: datetime = Field(default_factory=utcnow)
    is_valid: bool = False
    safety_score: float = Field(default=0.0, ge=0.0, le=1.0)
    brand_voice_score: float = Field(default=0.0, ge=0.0, le=1.0)
    format_compliant: bool = False
    issues: list[ValidationIssue] = Field(default_factory=list)
    requires_human_approval: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def blockers(self) -> list[ValidationIssue]:
        return [i for i in self.issues if i.severity is Severity.BLOCKER]


class CritiqueReport(SocialBase):
    """Structured feedback handed back to the Content Creation Agent."""

    draft_id: str
    attempt: int = Field(default=1, ge=1)
    must_fix: list[str] = Field(default_factory=list)
    should_fix: list[str] = Field(default_factory=list)
    keep: list[str] = Field(default_factory=list)
    revision_instructions: str = ""

    @classmethod
    def from_validation(cls, result: ValidationResult, attempt: int) -> CritiqueReport:
        must: list[str] = []
        should: list[str] = []
        for issue in result.issues:
            line = f"[{issue.code}] {issue.message}"
            if issue.suggestion:
                line = f"{line} -> {issue.suggestion}"
            (must if issue.severity is Severity.BLOCKER else should).append(line)
        return cls(
            draft_id=result.draft_id,
            attempt=attempt,
            must_fix=must,
            should_fix=should,
            revision_instructions=(
                "Rewrite the draft so every MUST FIX item is resolved. "
                "Preserve anything listed under KEEP verbatim."
            ),
        )


# ---------------------------------------------------------------------------
# Publishing Agent output
# ---------------------------------------------------------------------------
class PublishResult(SocialBase):
    draft_id: str
    platform: Platform
    status: PublishStatus
    platform_post_id: Optional[str] = None
    permalink: Optional[str] = None
    published_at: Optional[datetime] = None
    scheduled_for: Optional[datetime] = None
    error: Optional[str] = None


class ApprovalRequest(SocialBase):
    """A draft parked for human-in-the-loop review."""

    approval_id: str = Field(default_factory=lambda: new_id("apr"))
    draft_id: str
    platform: Platform
    reason: str = ""
    submitted_at: datetime = Field(default_factory=utcnow)
    validation: Optional[ValidationResult] = None


# ---------------------------------------------------------------------------
# Cross-cutting
# ---------------------------------------------------------------------------
class BrandGuideline(SocialBase):
    """A brand-voice snippet retrieved from the vector store."""

    doc_id: str
    text: str
    score: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PipelineError(SocialBase):
    node: str
    message: str
    occurred_at: datetime = Field(default_factory=utcnow)
    recoverable: bool = True


class UserGoals(SocialBase):
    """What the user asked this pipeline run to optimise for."""

    objective: str = "grow engagement"
    target_platforms: list[Platform] = Field(default_factory=list)
    posts_per_platform: int = Field(default=2, ge=1, le=20)
    horizon_days: int = Field(default=14, ge=1, le=90)
    tone: str = ""
    must_include: list[str] = Field(default_factory=list)
    must_avoid: list[str] = Field(default_factory=list)
    auto_publish: Optional[bool] = Field(
        default=None, description="None => fall back to the server-side default"
    )
