"""Scraper registry."""

from __future__ import annotations

from .base import BaseScraper, CardSelectors
from .portals import ALL_SCRAPERS

REGISTRY: dict[str, type[BaseScraper]] = {cls.name: cls for cls in ALL_SCRAPERS}

# The four portals the agent was asked for; the rest are opt-in extras.
DEFAULT_SOURCES: tuple[str, ...] = ("bazaraki", "index.cy", "scala.cy", "home.cy")

__all__ = ["BaseScraper", "CardSelectors", "REGISTRY", "DEFAULT_SOURCES"]
