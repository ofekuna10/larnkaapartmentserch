"""A connector that fabricates plausible data, for runs without credentials.

The point is that ``uvicorn main:app`` plus an empty ``.env`` gives you a
working pipeline end to end: analytics -> strategy -> content -> validation ->
approval queue. Numbers are seeded from the account id, so a given account
always produces the same history and tests stay deterministic.
"""

from __future__ import annotations

import random
from datetime import timedelta

from app.models.schemas import (
    ContentDraft,
    ContentFormat,
    Platform,
    PostMetrics,
    PublishResult,
    PublishStatus,
    new_id,
    utcnow,
)
from app.services.base import OAuthToken

_FORMATS = {
    Platform.YOUTUBE: (ContentFormat.SHORT_VIDEO, ContentFormat.LONG_VIDEO),
    Platform.INSTAGRAM: (ContentFormat.SHORT_VIDEO, ContentFormat.CAROUSEL),
    Platform.FACEBOOK: (ContentFormat.IMAGE_POST, ContentFormat.TEXT_POST),
    Platform.TIKTOK: (ContentFormat.SHORT_VIDEO,),
}

_TOPICS = (
    "behind the scenes",
    "customer story",
    "product teardown",
    "founder Q&A",
    "industry myth-busting",
    "how-to tutorial",
)


class SandboxConnector:
    """Stands in for a real platform adapter when no credentials are present."""

    def __init__(self, platform: Platform, *, posts: int = 24) -> None:
        self.platform = platform
        self.posts = posts

    async def fetch_recent_posts(
        self, token: OAuthToken, *, lookback_days: int, limit: int = 50
    ) -> list[PostMetrics]:
        rng = random.Random(f"{self.platform.value}:{token.account_id}")
        formats = _FORMATS[self.platform]
        now = utcnow()
        out: list[PostMetrics] = []
        for index in range(min(self.posts, limit)):
            age_days = rng.uniform(0, lookback_days)
            impressions = rng.randint(1_200, 90_000)
            # Short-form outperforms on engagement; the strategy agent should
            # be able to notice that in the summary.
            content_format = formats[index % len(formats)]
            base_rate = 0.06 if content_format is ContentFormat.SHORT_VIDEO else 0.025
            rate = max(0.002, rng.gauss(base_rate, base_rate / 3))
            interactions = int(impressions * rate)
            duration = (
                rng.uniform(12, 55)
                if content_format is ContentFormat.SHORT_VIDEO
                else rng.uniform(240, 900)
            )
            out.append(
                PostMetrics(
                    post_id=f"{self.platform.value}-sandbox-{index:03d}",
                    platform=self.platform,
                    published_at=now - timedelta(days=age_days),
                    content_format=content_format,
                    title=f"{_TOPICS[index % len(_TOPICS)].title()} #{index + 1}",
                    impressions=impressions,
                    reach=int(impressions * rng.uniform(0.6, 0.95)),
                    views=int(impressions * rng.uniform(0.4, 0.9)),
                    likes=int(interactions * 0.72),
                    comments=int(interactions * 0.11),
                    shares=int(interactions * 0.1),
                    saves=int(interactions * 0.07),
                    follows=rng.randint(0, 40),
                    duration_seconds=duration,
                    avg_view_duration_seconds=duration * rng.uniform(0.25, 0.8),
                )
            )
        return out

    async def publish(self, token: OAuthToken, draft: ContentDraft) -> PublishResult:
        post_id = new_id("sandbox")
        return PublishResult(
            draft_id=draft.draft_id,
            platform=self.platform,
            status=(
                PublishStatus.SCHEDULED if draft.scheduled_for else PublishStatus.PUBLISHED
            ),
            platform_post_id=post_id,
            permalink=f"https://sandbox.local/{self.platform.value}/{post_id}",
            published_at=None if draft.scheduled_for else utcnow(),
            scheduled_for=draft.scheduled_for,
        )

    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        return token.model_copy(update={"expires_at": utcnow() + timedelta(days=60)})


def sandbox_token(platform: Platform, user_id: str) -> OAuthToken:
    """A token good enough for the sandbox connector."""
    return OAuthToken(
        platform=platform,
        account_id=f"{platform.value}-{user_id}",
        access_token="sandbox",
        expires_at=utcnow() + timedelta(days=30),
        scopes=["sandbox"],
    )
