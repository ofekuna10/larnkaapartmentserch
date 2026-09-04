"""YouTube Data API v3 connector.

Reading is a two-call dance: ``search`` (or ``playlistItems``) gives you ids,
``videos`` gives you statistics. Publishing a video needs a binary upload,
which is the media service's job — this connector publishes metadata for an
already-uploaded asset and refuses drafts without one.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Optional

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

API_BASE = "https://www.googleapis.com/youtube/v3"
TOKEN_URL = "https://oauth2.googleapis.com/token"
UPLOAD_BASE = "https://www.googleapis.com/upload/youtube/v3"

_ISO_DURATION = re.compile(
    r"P(?:(?P<days>\d+)D)?T?(?:(?P<hours>\d+)H)?(?:(?P<minutes>\d+)M)?"
    r"(?:(?P<seconds>\d+)S)?"
)


def parse_iso_duration(value: Optional[str]) -> Optional[float]:
    """``PT1M35S`` -> 95.0 seconds."""
    if not value:
        return None
    match = _ISO_DURATION.fullmatch(value)
    if not match:
        return None
    parts = {k: int(v) for k, v in match.groupdict(default="0").items()}
    return float(
        parts["days"] * 86400
        + parts["hours"] * 3600
        + parts["minutes"] * 60
        + parts["seconds"]
    )


class YouTubeConnector(HttpConnector):
    platform = Platform.YOUTUBE

    def _auth_headers(self, token: OAuthToken) -> dict[str, str]:
        return {"Authorization": f"Bearer {token.access_token}"}

    # --- Analytics ---------------------------------------------------------
    async def fetch_recent_posts(
        self, token: OAuthToken, *, lookback_days: int, limit: int = 50
    ) -> list[PostMetrics]:
        cutoff = utcnow() - timedelta(days=lookback_days)
        search = await self.get_json(
            f"{API_BASE}/search",
            params={
                "part": "id",
                "forMine": "true",
                "type": "video",
                "order": "date",
                "maxResults": min(limit, 50),
                "publishedAfter": cutoff.strftime("%Y-%m-%dT%H:%M:%SZ"),
            },
            headers=self._auth_headers(token),
        )
        video_ids = [
            item["id"]["videoId"]
            for item in search.get("items", [])
            if item.get("id", {}).get("videoId")
        ]
        if not video_ids:
            return []

        details = await self.get_json(
            f"{API_BASE}/videos",
            params={
                "part": "snippet,statistics,contentDetails",
                "id": ",".join(video_ids),
                "maxResults": len(video_ids),
            },
            headers=self._auth_headers(token),
        )
        posts: list[PostMetrics] = []
        for item in details.get("items", []):
            stats = item.get("statistics", {})
            snippet = item.get("snippet", {})
            duration = parse_iso_duration(
                item.get("contentDetails", {}).get("duration")
            )
            published_at = None
            if snippet.get("publishedAt"):
                published_at = datetime.fromisoformat(
                    snippet["publishedAt"].replace("Z", "+00:00")
                )
            views = int(stats.get("viewCount") or 0)
            posts.append(
                PostMetrics(
                    post_id=item["id"],
                    platform=Platform.YOUTUBE,
                    published_at=published_at,
                    content_format=(
                        ContentFormat.SHORT_VIDEO
                        if duration is not None and duration <= 60
                        else ContentFormat.LONG_VIDEO
                    ),
                    title=snippet.get("title", "")[:120],
                    views=views,
                    # The Data API exposes no impression count; views is the
                    # closest denominator for an engagement rate.
                    impressions=views,
                    likes=int(stats.get("likeCount") or 0),
                    comments=int(stats.get("commentCount") or 0),
                    duration_seconds=duration,
                )
            )
        return posts

    # --- Publishing --------------------------------------------------------
    async def publish(self, token: OAuthToken, draft: ContentDraft) -> PublishResult:
        """Set metadata on an uploaded video, or schedule it as private.

        ``draft.media_asset_url`` must carry the YouTube video id produced by
        the resumable upload step (``youtube://<video_id>``); raw file uploads
        are handled outside the agent pipeline.
        """
        video_id = self._video_id(draft)
        body = {
            "id": video_id,
            "snippet": {
                "title": (draft.title or draft.hook)[:100],
                "description": draft.rendered_caption[:5000],
                "tags": draft.hashtags.all_tags[:15],
                "categoryId": "22",
            },
            "status": {
                "privacyStatus": "private" if draft.scheduled_for else "public",
                "selfDeclaredMadeForKids": False,
            },
        }
        if draft.scheduled_for:
            body["status"]["publishAt"] = draft.scheduled_for.strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )

        await self.post_json(
            f"{API_BASE}/videos?part=snippet,status",
            json=body,
            headers=self._auth_headers(token),
        )
        scheduled = bool(draft.scheduled_for)
        return PublishResult(
            draft_id=draft.draft_id,
            platform=Platform.YOUTUBE,
            status=PublishStatus.SCHEDULED if scheduled else PublishStatus.PUBLISHED,
            platform_post_id=video_id,
            permalink=f"https://www.youtube.com/watch?v={video_id}",
            published_at=None if scheduled else utcnow(),
            scheduled_for=draft.scheduled_for,
        )

    @staticmethod
    def _video_id(draft: ContentDraft) -> str:
        asset = draft.media_asset_url or ""
        if asset.startswith("youtube://"):
            return asset.removeprefix("youtube://")
        raise SocialAPIError(
            Platform.YOUTUBE,
            "draft has no uploaded video id (expected media_asset_url "
            "'youtube://<video_id>')",
        )

    # --- Tokens ------------------------------------------------------------
    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        if not token.refresh_token:
            raise SocialAPIError(Platform.YOUTUBE, "no refresh token stored")
        self.settings.require("youtube_client_id", "youtube_client_secret")
        payload = await self.post_json(
            TOKEN_URL,
            data={
                "client_id": self.settings.youtube_client_id,
                "client_secret": self.settings.youtube_client_secret,
                "refresh_token": token.refresh_token,
                "grant_type": "refresh_token",
            },
        )
        return token.model_copy(
            update={
                "access_token": payload["access_token"],
                "expires_at": utcnow()
                + timedelta(seconds=int(payload.get("expires_in", 3600))),
            }
        )
