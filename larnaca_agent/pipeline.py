"""The agent run: collect -> normalise -> geo-filter -> benchmark -> report."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional, Sequence

from .analysis import build_benchmarks, find_deals, summarise
from .config import DEFAULT_AREAS, THRESHOLDS, Thresholds
from .fetcher import Fetcher
from .geo import DriveTimeResolver, enrich_location
from .models import Deal, Listing
from .normalize import classify_resale, dedupe, is_plausible
from .scrapers import DEFAULT_SOURCES, REGISTRY

log = logging.getLogger(__name__)


@dataclass
class RunResult:
    deals: list[Deal]
    kept: list[Listing]
    summary: dict
    stats: dict = field(default_factory=dict)


@dataclass
class AgentConfig:
    areas: Sequence[str] = DEFAULT_AREAS
    sources: Sequence[str] = DEFAULT_SOURCES
    thresholds: Thresholds = THRESHOLDS
    max_pages: int = 3
    engine: str = "requests"
    osrm_url: Optional[str] = None
    cache_dir: Optional[Path] = Path(".cache")
    respect_robots: bool = True
    include_new_builds: bool = False
    # Detail pages to fetch per portal for listings whose size is missing.
    enrich_details: int = 0


def collect_with_reports(
    config: AgentConfig,
) -> tuple[list[Listing], dict[str, dict]]:
    """Run every selected scraper; return its listings and a per-portal report."""
    listings: list[Listing] = []
    reports: dict[str, dict] = {}

    with Fetcher(
        engine=config.engine,
        cache_dir=config.cache_dir,
        respect_robots=config.respect_robots,
    ) as fetcher:
        for name in config.sources:
            scraper_cls = REGISTRY.get(name)
            if scraper_cls is None:
                log.warning("unknown source %r — skipping", name)
                continue
            if scraper_cls.requires_browser and config.engine != "browser":
                log.warning(
                    "[%s] usually needs --engine browser; trying plain HTTP anyway",
                    name,
                )
            scraper = scraper_cls(fetcher, max_pages=config.max_pages)
            try:
                found = scraper.collect(config.areas)
            except Exception as exc:  # one broken portal must not kill the run
                log.error("[%s] scraper failed: %s", name, exc)
                scraper.report["errors"].append(str(exc))
                found = []

            if config.enrich_details:
                found = _enrich_missing_sizes(scraper, found, config.enrich_details)

            reports[name] = scraper.report
            log.info("[%s] collected %d raw listings", name, len(found))
            listings.extend(found)

    return listings, reports


def collect_listings(config: AgentConfig) -> list[Listing]:
    """Run every selected scraper and return the raw union of their results."""
    return collect_with_reports(config)[0]


def _enrich_missing_sizes(scraper, listings: list[Listing], budget: int) -> list[Listing]:
    """Fetch detail pages for listings that lack a size, up to ``budget`` pages.

    Without a covered area there is no €/m² and the listing is dropped later, so
    this converts otherwise-wasted results into usable ones.
    """
    spent = 0
    for listing in listings:
        if spent >= budget:
            break
        if listing.area_sqm is None and listing.price_eur:
            scraper.enrich_detail(listing)
            spent += 1
    if spent:
        log.info("[%s] fetched %d detail pages for missing sizes", scraper.name, spent)
        scraper.report["details_fetched"] = spent
    return listings


def load_listings(path: Path) -> list[Listing]:
    """Load listings from a JSON file (fixtures, or a previous --dump-listings)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload["listings"] if isinstance(payload, dict) else payload
    fields = set(Listing.__dataclass_fields__)
    return [Listing(**{k: v for k, v in row.items() if k in fields}) for row in rows]


def analyse(listings: Iterable[Listing], config: AgentConfig) -> RunResult:
    """Filter to the target set, benchmark it and pick out the bargains."""
    thresholds = config.thresholds
    stats = {"raw": 0, "implausible": 0, "new_build": 0, "outside_area": 0,
             "too_far": 0, "no_size": 0}

    listings = list(listings)
    stats["raw"] = len(listings)

    plausible = []
    for listing in listings:
        if is_plausible(listing):
            plausible.append(listing)
        else:
            stats["implausible"] += 1

    resolver = DriveTimeResolver(osrm_url=config.osrm_url)
    target_areas = set(config.areas)
    kept: list[Listing] = []

    for listing in dedupe(plausible):
        enrich_location(listing, resolver, text_area=listing.raw.get("area_hint"))

        is_resale, reason = classify_resale(listing)
        listing.is_resale, listing.resale_reason = is_resale, reason
        if not is_resale and not config.include_new_builds:
            stats["new_build"] += 1
            continue

        if listing.area_key not in target_areas:
            stats["outside_area"] += 1
            continue

        if (
            listing.drive_minutes_to_coast is None
            or listing.drive_minutes_to_coast > thresholds.max_drive_minutes_to_coast
        ):
            stats["too_far"] += 1
            continue

        if not listing.area_sqm:
            # Kept out of the benchmark and out of the results: without a size
            # there is no €/m² and no defensible discount.
            stats["no_size"] += 1
            continue

        kept.append(listing)

    benchmarks = build_benchmarks(kept, thresholds)
    deals = find_deals(kept, benchmarks, thresholds)
    summary = summarise(kept, benchmarks)
    summary["filter_stats"] = stats
    return RunResult(deals=deals, kept=kept, summary=summary, stats=stats)


def run(config: AgentConfig, listings: Optional[Iterable[Listing]] = None) -> RunResult:
    """Full agent run. Pass ``listings`` to analyse a fixture instead of crawling."""
    if listings is None:
        listings = collect_listings(config)
    return analyse(listings, config)
