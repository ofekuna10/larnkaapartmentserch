"""Shared plumbing for the social-platform connectors."""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, ConfigDict, Field

from app.core.config import Settings, get_settings
from app.models.schemas import (
    ContentDraft,
    Platform,
    PostMetrics,
    PublishResult,
    utcnow,
)

log = logging.getLogger(__name__)


class SocialAPIError(RuntimeError):
    """A platform API returned something we cannot act on."""

    def __init__(self, platform: Platform, message: str, status_code: int | None = None):
        super().__init__(f"{platform.value}: {message}")
        self.platform = platform
        self.status_code = status_code


class TokenExpiredError(SocialAPIError):
    """The stored OAuth token is no longer usable and refresh did not help."""


class OAuthToken(BaseModel):
    """A decrypted OAuth credential, as handed to a connector."""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    account_id: str
    access_token: str
    refresh_token: Optional[str] = None
    expires_at: Optional[datetime] = None
    scopes: list[str] = Field(default_factory=list)

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        # Treat "expires within a minute" as expired; the request would race.
        return self.expires_at <= utcnow() + timedelta(minutes=1)


@runtime_checkable
class SocialConnector(Protocol):
    """What every platform adapter must provide."""

    platform: Platform

    async def fetch_recent_posts(
        self, token: OAuthToken, *, lookback_days: int, limit: int = 50
    ) -> list[PostMetrics]: ...

    async def publish(self, token: OAuthToken, draft: ContentDraft) -> PublishResult: ...

    async def refresh_token(self, token: OAuthToken) -> OAuthToken: ...


class HttpConnector:
    """Base class with a retrying JSON HTTP client.

    Retries idempotent GETs on 429/5xx with jittered exponential backoff, and
    never retries a write — a duplicate post is worse than a failed one.
    """

    platform: Platform

    def __init__(
        self,
        settings: Optional[Settings] = None,
        client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=self.settings.http_timeout_seconds,
                headers={"User-Agent": "SocialAgentAI/1.0"},
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    async def get_json(
        self, url: str, *, params: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        client = await self._http()
        attempts = max(1, self.settings.http_max_retries)
        last_error: Optional[Exception] = None
        for attempt in range(attempts):
            try:
                response = await client.get(url, params=params, headers=headers)
                if response.status_code in (429, 500, 502, 503, 504):
                    raise SocialAPIError(
                        self.platform,
                        f"transient {response.status_code} from {url}",
                        response.status_code,
                    )
                if response.status_code in (401, 403):
                    raise TokenExpiredError(
                        self.platform, response.text[:300], response.status_code
                    )
                response.raise_for_status()
                return response.json()
            except TokenExpiredError:
                raise
            except (SocialAPIError, httpx.HTTPError) as exc:
                last_error = exc
                if attempt == attempts - 1:
                    break
                delay = min(2**attempt + random.uniform(0, 0.5), 20.0)
                log.warning(
                    "%s GET retry %s/%s in %.1fs (%s)",
                    self.platform.value, attempt + 1, attempts, delay, exc,
                )
                await asyncio.sleep(delay)
        raise SocialAPIError(self.platform, f"GET {url} failed: {last_error}")

    async def post_json(
        self, url: str, *, json: Optional[dict[str, Any]] = None,
        data: Optional[dict[str, Any]] = None,
        headers: Optional[dict[str, str]] = None,
    ) -> dict[str, Any]:
        """Single-shot write: no retries, so a post is never duplicated."""
        client = await self._http()
        try:
            response = await client.post(url, json=json, data=data, headers=headers)
        except httpx.HTTPError as exc:
            raise SocialAPIError(self.platform, f"POST {url} failed: {exc}") from exc
        if response.status_code in (401, 403):
            raise TokenExpiredError(
                self.platform, response.text[:300], response.status_code
            )
        if response.status_code >= 400:
            raise SocialAPIError(
                self.platform,
                f"POST {url} -> {response.status_code}: {response.text[:300]}",
                response.status_code,
            )
        return response.json() if response.content else {}

    async def refresh_token(self, token: OAuthToken) -> OAuthToken:
        """Platforms that support refresh override this."""
        raise TokenExpiredError(
            self.platform, "token expired and this platform has no refresh flow"
        )
