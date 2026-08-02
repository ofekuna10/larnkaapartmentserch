"""Command line entry point: ``python -m larnaca_agent``."""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import AREAS, DEFAULT_AREAS, THRESHOLDS, Thresholds
from .geo import DEFAULT_OSRM_URL
from .pipeline import AgentConfig, collect_with_reports, load_listings, run
from .report import (
    render_csv,
    render_diagnostics,
    render_json,
    render_markdown,
    render_source_table,
)
from .scrapers import DEFAULT_SOURCES, REGISTRY
from .state import SeenStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="larnaca-agent",
        description=(
            "Find second-hand apartments in central Larnaca (Livadia, Finikoudes, "
            "Mackenzie) within a short drive of the coast and priced at least 15% "
            "below the local market."
        ),
    )
    parser.add_argument(
        "--areas",
        nargs="+",
        default=list(DEFAULT_AREAS),
        choices=sorted(AREAS),
        help="target neighbourhoods (default: %(default)s)",
    )
    parser.add_argument(
        "--sources",
        nargs="+",
        default=list(DEFAULT_SOURCES),
        choices=sorted(REGISTRY),
        help="portals to crawl (default: %(default)s)",
    )
    parser.add_argument(
        "--max-drive-min",
        type=float,
        default=THRESHOLDS.max_drive_minutes_to_coast,
        help="maximum drive time to the coastline, in minutes (default: %(default)s)",
    )
    parser.add_argument(
        "--discount",
        type=float,
        default=THRESHOLDS.min_discount * 100,
        help="minimum discount vs. the area benchmark, in %% (default: %(default)s)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=3,
        help="results pages to crawl per search URL (default: %(default)s)",
    )
    parser.add_argument(
        "--engine",
        choices=("requests", "browser"),
        default="requests",
        help="'browser' drives headless Chromium via Playwright, needed for the "
             "portals behind bot protection (default: %(default)s)",
    )
    parser.add_argument(
        "--osrm-url",
        default=DEFAULT_OSRM_URL,
        help="OSRM routing server for real drive times; pass '' to always use "
             "the straight-line estimate (default: %(default)s)",
    )
    parser.add_argument(
        "--include-new-builds",
        action="store_true",
        help="do not filter out new-build / off-plan adverts",
    )
    parser.add_argument(
        "--enrich-details",
        type=int,
        default=0,
        metavar="N",
        help="fetch up to N advert pages per portal to recover a missing size "
             "(listings without a size cannot be benchmarked)",
    )
    parser.add_argument(
        "--diagnose",
        action="store_true",
        help="crawl one page per portal and report what each one returned and "
             "which extraction layer worked",
    )
    parser.add_argument("--no-cache", action="store_true", help="bypass the HTTP cache")
    parser.add_argument(
        "--ignore-robots",
        action="store_true",
        help="skip the robots.txt check (use only where you are permitted to)",
    )
    parser.add_argument(
        "--from-file",
        type=Path,
        help="analyse listings from a JSON file instead of crawling",
    )
    parser.add_argument(
        "--dump-listings",
        type=Path,
        help="write every collected listing to this JSON file",
    )
    parser.add_argument(
        "--format",
        choices=("markdown", "json", "csv"),
        default="markdown",
        help="output format (default: %(default)s)",
    )
    parser.add_argument("--out", type=Path, help="write the report to this file too")
    parser.add_argument(
        "--only-new",
        action="store_true",
        help="report only listings not seen in a previous run (or newly reduced)",
    )
    parser.add_argument(
        "--state-file",
        type=Path,
        default=Path("seen_listings.json"),
        help="where --only-new keeps its history (default: %(default)s)",
    )
    parser.add_argument(
        "--watch",
        type=int,
        metavar="MINUTES",
        help="keep running every MINUTES minutes (implies --only-new)",
    )
    parser.add_argument("-v", "--verbose", action="count", default=0)
    return parser


def _config_from_args(args: argparse.Namespace) -> AgentConfig:
    thresholds = Thresholds(
        max_drive_minutes_to_coast=args.max_drive_min,
        min_discount=args.discount / 100.0,
    )
    return AgentConfig(
        areas=args.areas,
        sources=args.sources,
        thresholds=thresholds,
        max_pages=args.max_pages,
        engine=args.engine,
        osrm_url=args.osrm_url or None,
        cache_dir=None if args.no_cache else Path(".cache"),
        respect_robots=not args.ignore_robots,
        include_new_builds=args.include_new_builds,
        enrich_details=args.enrich_details,
    )


def _diagnose(config: AgentConfig) -> int:
    """Crawl a single page per portal and explain what each one did."""
    config = replace(config, max_pages=1)
    listings, reports = collect_with_reports(config)
    print(render_diagnostics(reports, listings))
    working = sum(1 for report in reports.values() if report.get("listings"))
    print(f"{working}/{len(reports)} portals returned listings.")
    return 0 if working else 1


def _one_pass(args: argparse.Namespace, config: AgentConfig) -> int:
    reports: dict[str, dict] = {}
    if args.from_file:
        listings = load_listings(args.from_file)
    else:
        listings, reports = collect_with_reports(config)

    if args.dump_listings:
        args.dump_listings.parent.mkdir(parents=True, exist_ok=True)
        args.dump_listings.write_text(
            json.dumps([l.to_dict() for l in listings], indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    result = run(config, listings=listings)
    deals = result.deals

    store = None
    if args.only_new or args.watch:
        store = SeenStore(args.state_file)
        deals = store.filter_new(deals)

    if args.format == "json":
        output = render_json(deals, result.summary)
    elif args.format == "csv":
        output = render_csv(deals)
    else:
        output = render_markdown(deals, result.summary)
        table = render_source_table(reports)
        if table:
            output += "\n\n" + table

    print(output)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")

    if store is not None:
        store.remember(deals)
        store.save()

    if not listings:
        print(
            "\nNo listings were retrieved. If the portals returned 403, rerun with "
            "--engine browser (pip install playwright && playwright install chromium).",
            file=sys.stderr,
        )
        return 2
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=[logging.WARNING, logging.INFO, logging.DEBUG][min(args.verbose, 2)],
        format="%(levelname)s %(name)s: %(message)s",
    )
    config = _config_from_args(args)

    if args.diagnose:
        return _diagnose(config)

    if not args.watch:
        return _one_pass(args, config)

    while True:
        try:
            _one_pass(args, config)
        except KeyboardInterrupt:
            return 130
        except Exception as exc:
            logging.error("run failed: %s", exc)
        print(f"\n--- sleeping {args.watch} min ---\n", file=sys.stderr)
        time.sleep(args.watch * 60)


if __name__ == "__main__":
    raise SystemExit(main())
