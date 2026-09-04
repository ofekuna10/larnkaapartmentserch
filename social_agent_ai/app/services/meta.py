"""Meta Graph API connector — covers both Instagram Business and Facebook Pages.

Both surfaces share one API host and one token type, so they share one adapter
parameterised by :class:`Platform`. Only the field names and the publish flow
differ, and those differences are isolated in the ``_ig_*`` / ``_fb_*`` methods.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from app.models.schemas import (
    ContentDraft,
    ContentFormat,
    Platform,
    PostMetrics,
    PublishResult,
    PublishStatus,
    utcnow,
)
from app.services.base import HttpConnector, OAuthToken, SocialAPIError

_IG_MEDIA_FIELDS = (
    "id,caption,media_type,media_product_type,timestamp,permalink,"
    "like_count,comments_count"
)
_IG_INSIGHT_METRICS = "impressions,reach,saved,shares,total_interactions"
_IG_VIDEO_METRICS = "impressions,reach,saved,shares,total_interactions,ig_reels_avg_watch_time"

_FB_POST_FIELDS = (
    "id,message,created_time,permalink_url,shares,"
    "likes.summary(true),comments.summary(true)"
)
_FB_INSIGHT_METRICS = "post_impressions,post_impressions_unique,post_video_views"

_IG_FORMAT_MAP = {
    "REELS": ContentFormat.SHORT_VIDEO,
    "VIDEO": ContentFormat.SHORT_VIDEO,
    "IMAGE": ContentFormat.IMAGE_POST,
    "CAROUSEL_ALBUM": ContentFormat.CAROUSEL,
    "STORY": ContentFormat.STORY,
}


def _parse_meta_time(value: Optional[str]) -> Optional[datetime]:
    """Graph API timestamps look like ``2024-05-01T12:00:00+0000``."""
    if not value:
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%S%z")
    except ValueError:
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None


class MetaConnector(HttpConnector):
    """Instagram Business / Facebook Page adapter."""

    def __init__(self, platform: Platform, **kwargs: Any) -> None:
        if platform not in (Platform.INSTAGRAM, Platform.FACEBOOK):
            raise ValueError(f"MetaConnector does not serve {platform}")
        self.platform = platform
        super().__init__(**kwargs)

    @property
    def base_url(self) -> str:
        return self.settings.meta_graph_base_url

    # --- Analytics ---------------------------------------------------------
    async def fetch_recent_posts(
        self, token: OAuthToken, *, lookback_days: int, limit: int = 50
    ) -> list[PostMetrics]:
        cutoff = utcnow() - timedelta(days=lookback_days)
        if self.platform is Platform.INSTAGRAM:
            return await self._ig_fetch(token, cutoff, limit)
        return await self._fb_fetch(token, cutoff, limit)

    async def _ig_fetch(
        self, token: OAuthToken, cutoff: datetime, limit: int
    ) -> list[PostMetrics]:
        payload = await self.get_json(
            f"{self.base_url}/{token.account_id}/media",
            params={
                "fields": _IG_MEDIA_FIELDS,
                "limit": min(limit, 100),
                "access_token": token.access_token,
            },
        )
        posts: list[PostMetrics] = []
        for item in payload.get("data", []):
            published_at = _parse_meta_time(item.get("timestamp"))
            if published_at and published_at < cutoff:
                continue
            product_type = item.get("media_product_type") or item.get("media_type", "")
            metrics = PostMetrics(
                post_id=item["id"],
                platform=Platform.INSTAGRAM,
                published_at=published_at,
                content_format=_IG_FORMAT_MAP.get(product_type.upper()),
                title=(item.get("caption") or "")[:120],
                likes=int(item.get("like_count") or 0),
                comments=int(item.get("comments_count") or 0),
            )
            await self._ig_apply_insights(token, item["id"], product_type, metrics)
            posts.append(metrics)
        return posts

    async def _ig_apply_insights(
        self, token: OAuthToken, media_id: str, product_type: str, metrics: PostMetrics
    ) -> None:
        """Insights live on a separate edge and 400 for unsupported metrics."""
        wanted = (
            _IG_VIDEO_METRICS
            if product_type.upper() in ("REELS", "VIDEO")
            else _IG_INSIGHT_METRICS
        )
        try:
            payload = await self.get_json(
                f"{self.base_url}/{media_id}/insights",
                params={"metric": wanted, "access_token": token.access_token},
            )
        except SocialAPIError:
            return  # Insights are best-effort; the base counts still stand.
        for entry in payload.get("data", []):
            values = entry.get("values") or [{}]
            value = values[0].get("value", 0)
            name = entry.get("name")
            if name == "impressions":
                metrics.impressions = int(value or 0)
            elif name == "reach":
                metrics.reach = int(value or 0)
            elif name == "saved":
                metrics.saves = int(value or 0)
            elif name == "shares":
                metrics.shares = int(value or 0)
            elif name == "ig_reels_avg_watch_time":
                metrics.avg_view_duration_seconds = float(value or 0) / 1000

    async def _fb_fetch(
        self, token: OAuthToken, cutoff: datetime, limit: int
    ) -> list[PostMetrics]:
        payload = await self.get_json(
            f"{self.base_url}/{token.account_id}/posts",
            params={
                "fields": _FB_POST_FIELDS,
                "limit": min(limit, 100),
                "access_token": token.access_token,
            },
        )
        posts: list[PostMetrics] = []
        for item in payload.get("data", []):
            published_at = _parse_meta_time(item.get("created_time"))
            if published_at and published_at < cutoff:
                continue
            metrics = PostMetrics(
                post_id=item["id"],
                platform=Platform.FACEBOOK,
                published_at=published_at,
                title=(item.get("message") or "")[:120],
                likes=int((item.get("likes") or {}).get("summary", {}).get("total_count", 0)),
                comments=int(
                    (item.get("comments") or {}).get("summary", {}).get("total_count", 0)
                ),
                shares=int((item.get("shares") or {}).get("count", 0)),
            )
            try:
                insights = await self.get_json(
                    f"{self.base_url}/{item['id']}/insights",
                    params={
                        "metric": _FB_INSIGHT_METRICS,
                        "access_token": token.access_token,
                    },
                )
            except SocialAPIError:
                insights = {}
            for entry in insights.get("data", []):
                value = (entry.get("values") or [{}])[0].get("value", 0)
                if entry.get("name") == "post_impressions":
                    metrics.impressions = int(value or 0)
                elif entry.get("name") == "post_impressions_unique":
                    metrics.reach = int(value or 0)
                elif entry.get("name") == "post_video_views":
                    metrics.views = int(value or 0)
            posts.append(metrics)
        return posts

    # --- Publishing --------------------------------------------------------
    async def publish(self, token: OAuthToken, draft: ContentDraft) -> PublishResult:
        if self.platform is Platform.INSTAGRAM:
            return await self._ig_publish(token, draft)
        return await self._fb_publish(token, draft)

    async def _ig_publish(
        self, token: OAuthToken, draft: ContentDraft
    ) -> PublishResult:
        """Two-step container flow: create the media, then publish it."""
        if not draft.media_asset_url:
            raise SocialAPIError(
                Platform.INSTAGRAM, "Instagram requires a rendered media asset"
            )
        container_params: dict[str, Any] = {
            "caption": draft.rendered_caption,
            "access_token": token.access_token,
        }
        if draft.content_format is ContentFormat.SHORT_VIDEO:
            container_params["media_type"] = "REELS"
            container_params["video_url"] = draft.media_asset_url
        else:
            container_params["image_url"] = draft.media_asset_url

        container = await self.post_json(
            f"{self.base_url}/{token.account_id}/media", data=container_params
        )
        creation_id = container.get("id")
        if not creation_id:
            raise SocialAPIError(Platform.INSTAGRAM, f"no container id in {container}")

        published = await self.post_json(
            f"{self.base_url}/{token.account_id}/media_publish",
            data={"creation_id": creation_id, "access_token": token.access_token},
        )
        post_id = published.get("id", creation_id)
        return PublishResult(
            draft_id=draft.draft_id,
            platform=Platform.INSTAGRAM,
            status=PublishStatus.PUBLISHED,
            platform_post_id=post_id,
            permalink=f"https://www.instagram.com/p/{post_id}/",
            published_at=utcnow(),
        )

    async def _fb_publish(
        self, token: OAuthToken, draft: ContentDraft
    ) -> PublishResult:
        params: dict[str, Any] = {
            "message": draft.rendered_caption,
            "access_token": token.access_token,
        }
        endpoint = f"{self.base_url}/{token.account_id}/feed"
        if draft.media_asset_url:
            endpoint = f"{self.base_url}/{token.account_id}/photos"
            params["url"] = draft.media_asset_url
        if draft.scheduled_for:
            params["published"] = "false"
            params["scheduled_publish_time"] = int(draft.scheduled_for.timestamp())

        result = await self.post_json(endpoint, data=params)
        post_id = result.get("post_id") or result.get("id", "")
        scheduled = bool(draft.scheduled_for)
        return PublishResult(
            draft_id=draft.draft_id,
            platform=Platform.FACEBOOK,
            status=PublishStatus.SCHEDULED if scheduled else PublishStatus.PUBLISHED,
            platform_post_id=post_id,
            permalink=f"https://www.facebook.com/{post_id}" if post_id else None,
            published_at=None if scheduled else utcnow(),
            scheduled_for=draft.scheduled_for,
        )

    # --- Tokens ------------------------------------------------------------
    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        """Exchange a short-lived user token for a long-lived one (60 days)."""
        self.settings.require("meta_app_id", "meta_app_secret")
        payload = await self.get_json(
            f"{self.base_url}/oauth/access_token",
            params={
                "grant_type": "fb_exchange_token",
                "client_id": self.settings.meta_app_id,
                "client_secret": self.settings.meta_app_secret,
                "fb_exchange_token": token.access_token,
            },
        )
        expires_in = int(payload.get("expires_in") or 0)
        return token.model_copy(
            update={
                "access_token": payload["access_token"],
                "expires_at": utcnow() + timedelta(seconds=expires_in)
                if expires_in
                else None,
            }
        )
