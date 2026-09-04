"""Resolution of platform connectors, token stores and vector stores.

Every agent node asks this module for its dependencies, and tests override
them with :func:`set_connector_factory` / :func:`set_token_store` instead of
patching imports.
"""

from __future__ import annotations

import logging
from typing import Callable, Optional

from app.core.config import Settings, get_settings
from app.models.schemas import Platform
from app.services.base import SocialConnector
from app.services.meta import MetaConnector
from app.services.sandbox import SandboxConnector
from app.services.tiktok import TikTokConnector
from app.services.token_store import InMemoryTokenStore, TokenStore
from app.services.youtube import YouTubeConnector

log = logging.getLogger(__name__)

ConnectorFactory = Callable[[Platform], SocialConnector]

_connector_factory: Optional[ConnectorFactory] = None
_token_store: Optional[TokenStore] = None


def _credentials_present(platform: Platform, settings: Settings) -> bool:
    if platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
        return bool(settings.meta_app_id and settings.meta_app_secret)
    if platform is Platform.YOUTUBE:
        return bool(settings.youtube_client_id and settings.youtube_client_secret)
    if platform is Platform.TIKTOK:
        return bool(settings.tiktok_client_key and settings.tiktok_client_secret)
    return False


def default_connector(platform: Platform) -> SocialConnector:
    """Live connector when the platform app is configured, sandbox otherwise."""
    settings = get_settings()
    if not _credentials_present(platform, settings):
        log.info("no %s credentials configured; using sandbox", platform.value)
        return SandboxConnector(platform)
    if platform in (Platform.INSTAGRAM, Platform.FACEBOOK):
        return MetaConnector(platform)
    if platform is Platform.YOUTUBE:
        return YouTubeConnector()
    return TikTokConnector()


def connector_for(platform: Platform) -> SocialConnector:
    factory = _connector_factory or default_connector
    return factory(platform)


def set_connector_factory(factory: Optional[ConnectorFactory]) -> None:
    """Install a factory (tests, or a deployment with bespoke adapters)."""
    global _connector_factory
    _connector_factory = factory


def get_token_store() -> TokenStore:
    global _token_store
    if _token_store is None:
        _token_store = InMemoryTokenStore()
    return _token_store


def set_token_store(store: Optional[TokenStore]) -> None:
    global _token_store
    _token_store = store
