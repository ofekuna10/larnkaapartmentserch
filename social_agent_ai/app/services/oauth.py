"""OAuth authorisation URLs and code exchange, per platform.

Kept out of the router so the HTTP layer stays thin and the flows can be
tested directly. Every exchange finishes by resolving the *account id* the
connectors need (an IG business account, a YouTube channel, a TikTok open id),
because a bare access token is not enough to call any of these APIs.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any
from urllib.parse import urlencode

import httpx

from app.core.config import Settings, get_settings
from app.models.schemas import Platform, utcnow
from app.services.base import OAuthToken, SocialAPIError

log = logging.getLogger(__name__)

SCOPES: dict[Platform, tuple[str, ...]] = {
    Platform.INSTAGRAM: (
        "instagram_basic",
        "instagram_content_publish",
        "instagram_manage_insights",
        "pages_show_list",
        "pages_read_engagement",
    ),
    Platform.FACEBOOK: (
        "pages_show_list",
        "pages_read_engagement",
        "pages_manage_posts",
        "read_insights",
    ),
    Platform.YOUTUBE: (
        "https://www.googleapis.com/auth/youtube.readonly",
        "https://www.googleapis.com/auth/youtube.upload",
        "https://www.googleapis.com/auth/yt-analytics.readonly",
    ),
    Platform.TIKTOK: ("user.info.basic", "video.list", "video.publish"),
}


def _redirect_uri(platform: Platform, settings: Settings) -> str:
    if platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
        uri = settings.meta_redirect_uri
    elif platform is Platform.YOUTUBE:
        uri = settings.youtube_redirect_uri
    else:
        uri = settings.tiktok_redirect_uri
    if not uri:
        raise SocialAPIError(platform, "no redirect URI configured for this platform")
    return uri


def authorization_url(platform: Platform, state: str) -> str:
    """The URL to send the user to in order to grant access."""
    settings = get_settings()
    redirect_uri = _redirect_uri(platform, settings)
    scope = SCOPES[platform]

    if platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
        settings.require("meta_app_id")
        query = urlencode(
            {
                "client_id": settings.meta_app_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": ",".join(scope),
                "response_type": "code",
            }
        )
        return (
            f"https://www.facebook.com/{settings.meta_graph_version}/dialog/oauth?{query}"
        )

    if platform is Platform.YOUTUBE:
        settings.require("youtube_client_id")
        query = urlencode(
            {
                "client_id": settings.youtube_client_id,
                "redirect_uri": redirect_uri,
                "state": state,
                "scope": " ".join(scope),
                "response_type": "code",
                # offline + consent is what actually returns a refresh token.
                "access_type": "offline",
                "prompt": "consent",
                "include_granted_scopes": "true",
            }
        )
        return f"https://accounts.google.com/o/oauth2/v2/auth?{query}"

    settings.require("tiktok_client_key")
    query = urlencode(
        {
            "client_key": settings.tiktok_client_key,
            "redirect_uri": redirect_uri,
            "state": state,
            "scope": ",".join(scope),
            "response_type": "code",
        }
    )
    return f"https://www.tiktok.com/v2/auth/authorize/?{query}"


async def exchange_code(platform: Platform, code: str) -> OAuthToken:
    """Swap an authorisation code for a stored-ready credential."""
    settings = get_settings()
    redirect_uri = _redirect_uri(platform, settings)
    async with httpx.AsyncClient(timeout=settings.http_timeout_seconds) as client:
        if platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
            return await _exchange_meta(client, platform, code, redirect_uri, settings)
        if platform is Platform.YOUTUBE:
            return await _exchange_google(client, code, redirect_uri, settings)
        return await _exchange_tiktok(client, code, redirect_uri, settings)


def _json_or_raise(
    platform: Platform, response: httpx.Response
) -> dict[str, Any]:
    if response.status_code >= 400:
        raise SocialAPIError(
            platform, f"{response.status_code}: {response.text[:300]}",
            response.status_code,
        )
    return response.json()


async def _exchange_meta(
    client: httpx.AsyncClient,
    platform: Platform,
    code: str,
    redirect_uri: str,
    settings: Settings,
) -> OAuthToken:
    settings.require("meta_app_id", "meta_app_secret")
    base = settings.meta_graph_base_url
    payload = _json_or_raise(
        platform,
        await client.get(
            f"{base}/oauth/access_token",
            params={
                "client_id": settings.meta_app_id,
                "client_secret": settings.meta_app_secret,
                "redirect_uri": redirect_uri,
                "code": code,
            },
        ),
    )
    user_token = payload["access_token"]

    # Publishing happens as a Page, so resolve the Page (and, for Instagram,
    # the business account attached to it) and keep the Page token.
    accounts = _json_or_raise(
        platform,
        await client.get(
            f"{base}/me/accounts",
            params={
                "fields": "id,name,access_token,instagram_business_account",
                "access_token": user_token,
            },
        ),
    )
    pages = accounts.get("data") or []
    if not pages:
        raise SocialAPIError(platform, "the granting user manages no Pages")
    page = pages[0]
    page_token = page.get("access_token", user_token)

    if platform is Platform.INSTAGRAM:
        ig_account = (page.get("instagram_business_account") or {}).get("id")
        if not ig_account:
            raise SocialAPIError(
                platform, f"Page {page.get('id')} has no linked Instagram account"
            )
        account_id = ig_account
    else:
        account_id = page["id"]

    expires_in = int(payload.get("expires_in") or 0)
    return OAuthToken(
        platform=platform,
        account_id=str(account_id),
        access_token=page_token,
        expires_at=utcnow() + timedelta(seconds=expires_in) if expires_in else None,
        scopes=list(SCOPES[platform]),
    )


async def _exchange_google(
    client: httpx.AsyncClient, code: str, redirect_uri: str, settings: Settings
) -> OAuthToken:
    settings.require("youtube_client_id", "youtube_client_secret")
    payload = _json_or_raise(
        Platform.YOUTUBE,
        await client.post(
            "https://oauth2.googleapis.com/token",
            data={
                "client_id": settings.youtube_client_id,
                "client_secret": settings.youtube_client_secret,
                "redirect_uri": redirect_uri,
                "grant_type": "authorization_code",
                "code": code,
            },
        ),
    )
    access_token = payload["access_token"]
    channels = _json_or_raise(
        Platform.YOUTUBE,
        await client.get(
            "https://www.googleapis.com/youtube/v3/channels",
            params={"part": "id", "mine": "true"},
            headers={"Authorization": f"Bearer {access_token}"},
        ),
    )
    items = channels.get("items") or []
    if not items:
        raise SocialAPIError(Platform.YOUTUBE, "the granting account has no channel")
    return OAuthToken(
        platform=Platform.YOUTUBE,
        account_id=items[0]["id"],
        access_token=access_token,
        refresh_token=payload.get("refresh_token"),
        expires_at=utcnow() + timedelta(seconds=int(payload.get("expires_in", 3600))),
        scopes=list(SCOPES[Platform.YOUTUBE]),
    )


async def _exchange_tiktok(
    client: httpx.AsyncClient, code: str, redirect_uri: str, settings: Settings
) -> OAuthToken:
    settings.require("tiktok_client_key", "tiktok_client_secret")
    payload = _json_or_raise(
        Platform.TIKTOK,
        await client.post(
            "https://open.tiktokapis.com/v2/oauth/token/",
            data={
                "client_key": settings.tiktok_client_key,
                "client_secret": settings.tiktok_client_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        ),
    )
    if not payload.get("access_token"):
        raise SocialAPIError(Platform.TIKTOK, f"no access_token in {payload}")
    return OAuthToken(
        platform=Platform.TIKTOK,
        account_id=str(payload.get("open_id", "")),
        access_token=payload["access_token"],
        refresh_token=payload.get("refresh_token"),
        expires_at=utcnow() + timedelta(seconds=int(payload.get("expires_in", 86400))),
        scopes=list(SCOPES[Platform.TIKTOK]),
    )
