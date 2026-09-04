"""TikTok Content Posting & Display API connector (v2).

Publishing is a pull-from-URL flow: we hand TikTok a public asset URL and it
fetches the file itself, so the pipeline never streams bytes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

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

API_BASE = "https://open.tiktokapis.com/v2"
TOKEN_URL = f"{API_BASE}/oauth/token/"

_VIDEO_FIELDS = (
    "id,create_time,title,video_description,duration,cover_image_url,share_url,"
    "view_count,like_count,comment_count,share_count"
)


class TikTokConnector(HttpConnector):
    platform = Platform.TIKTOK

    def _auth_headers(self, token: OAuthToken) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {token.access_token}",
            "Content-Type": "application/json; charset=UTF-8",
        }

    # --- Analytics ---------------------------------------------------------
    async def fetch_recent_posts(
        self, token: OAuthToken, *, lookback_days: int, limit: int = 50
    ) -> list[PostMetrics]:
        cutoff = utcnow() - timedelta(days=lookback_days)
        payload = await self.post_json(
            f"{API_BASE}/video/list/?fields={_VIDEO_FIELDS}",
            json={"max_count": min(limit, 20)},
            headers=self._auth_headers(token),
        )
        videos = (payload.get("data") or {}).get("videos", [])
        posts: list[PostMetrics] = []
        for item in videos:
            created = item.get("create_time")
            published_at = (
                datetime.fromtimestamp(int(created), tz=timezone.utc)
                if created
                else None
            )
            if published_at and published_at < cutoff:
                continue
            views = int(item.get("view_count") or 0)
            posts.append(
                PostMetrics(
                    post_id=str(item.get("id", "")),
                    platform=Platform.TIKTOK,
                    published_at=published_at,
                    content_format=ContentFormat.SHORT_VIDEO,
                    title=(item.get("title") or item.get("video_description") or "")[:120],
                    views=views,
                    # TikTok reports no impressions; views is the denominator.
                    impressions=views,
                    likes=int(item.get("like_count") or 0),
                    comments=int(item.get("comment_count") or 0),
                    shares=int(item.get("share_count") or 0),
                    duration_seconds=float(item.get("duration") or 0) or None,
                )
            )
        return posts

    # --- Publishing --------------------------------------------------------
    async def publish(self, token: OAuthToken, draft: ContentDraft) -> PublishResult:
        if not draft.media_asset_url:
            raise SocialAPIError(Platform.TIKTOK, "TikTok requires a video asset URL")
        payload = await self.post_json(
            f"{API_BASE}/post/publish/video/init/",
            json={
                "post_info": {
                    "title": draft.rendered_caption[:2200],
                    "privacy_level": "PUBLIC_TO_EVERYONE",
                    "disable_duet": False,
                    "disable_comment": False,
                    "disable_stitch": False,
                },
                "source_info": {
                    "source": "PULL_FROM_URL",
                    "video_url": draft.media_asset_url,
                },
            },
            headers=self._auth_headers(token),
        )
        data = payload.get("data") or {}
        publish_id = data.get("publish_id")
        error = payload.get("error") or {}
        if error.get("code") not in (None, "ok"):
            raise SocialAPIError(Platform.TIKTOK, str(error))
        if not publish_id:
            raise SocialAPIError(Platform.TIKTOK, f"no publish_id in {payload}")
        # TikTok finishes the upload asynchronously and confirms by webhook.
        return PublishResult(
            draft_id=draft.draft_id,
            platform=Platform.TIKTOK,
            status=PublishStatus.SCHEDULED,
            platform_post_id=publish_id,
            published_at=None,
            scheduled_for=draft.scheduled_for,
        )

    # --- Tokens ------------------------------------------------------------
    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        if not token.refresh_token:
            raise SocialAPIError(Platform.TIKTOK, "no refresh token stored")
        self.settings.require("tiktok_client_key", "tiktok_client_secret")
        payload = await self.post_json(
            TOKEN_URL,
            data={
                "client_key": self.settings.tiktok_client_key,
                "client_secret": self.settings.tiktok_client_secret,
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        return token.model_copy(
            update={
                "access_token": payload["access_token"],
                "refresh_token": payload.get("refresh_token", token.refresh_token),
                "expires_at": utcnow()
                + timedelta(seconds=int(payload.get("expires_in", 86400))),
            }
        )
