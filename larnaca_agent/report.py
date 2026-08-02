"""Rendering: a chat-ready Markdown digest, plus JSON and CSV exports."""

from __future__ import annotations

import csv
import io
import json
from typing import Sequence

from .config import AREAS
from .models import Deal


def _area_name(area_key: str | None) -> str:
    if not area_key:
        return "unknown area"
    area = AREAS.get(area_key)
    return area.display_name if area else area_key


def _money(value: float | None) -> str:
    return f"€{value:,.0f}" if value else "—"


def _drive_time(minutes: float | None) -> str | None:
    if minutes is None:
        return None
    return "<1 min to the sea" if minutes < 1 else f"{minutes:.0f} min to the sea"


def render_markdown(deals: Sequence[Deal], summary: dict, *, title: str = "") -> str:
    """The digest that gets pasted into chat."""
    lines: list[str] = []
    lines.append(title or "## Larnaca resale apartments — below-market finds")
    lines.append("")

    considered = summary.get("listings_considered", 0)
    by_area = summary.get("listings_by_area", {})
    area_counts = ", ".join(
        f"{_area_name(key)}: {count}" for key, count in sorted(by_area.items())
    )
    lines.append(
        f"Scanned **{considered}** matching resale listings"
        + (f" ({area_counts})" if area_counts else "")
        + f" — **{len(deals)}** priced 15%+ below their area benchmark."
    )
    lines.append("")

    benchmarks = summary.get("benchmarks", {})
    if benchmarks:
        lines.append("### Market benchmark (median €/m², resale, within the drive-time ring)")
        lines.append("")
        lines.append("| Area | Median €/m² | P25–P75 €/m² | Median asking price | Sample |")
        lines.append("|---|---:|---:|---:|---:|")
        for key, stats in sorted(benchmarks.items()):
            name = "All target areas" if key == "__combined__" else _area_name(key)
            flag = " ⚠️" if stats.get("low_confidence") else ""
            lines.append(
                f"| {name}{flag} | €{stats['median_eur_per_sqm']:,} "
                f"| €{stats['p25_eur_per_sqm']:,}–€{stats['p75_eur_per_sqm']:,} "
                f"| {_money(stats['median_price_eur'])} | {stats['sample']} |"
            )
        lines.append("")

    if not deals:
        lines.append("No listing currently clears the 15% threshold.")
        return "\n".join(lines)

    lines.append("### Findings")
    lines.append("")
    for index, deal in enumerate(deals, start=1):
        listing = deal.listing
        header = listing.title.strip() or "Apartment"
        lines.append(f"**{index}. {header}**")
        details = [
            _money(listing.price_eur),
            f"{listing.area_sqm:.0f} m²" if listing.area_sqm else None,
            f"{listing.bedrooms} bed" if listing.bedrooms is not None else None,
            f"built {listing.year_built}" if listing.year_built else None,
            _area_name(listing.area_key),
            _drive_time(listing.drive_minutes_to_coast),
        ]
        lines.append("- " + " · ".join(d for d in details if d))
        lines.append(
            f"- **{deal.discount * 100:.0f}% below the area benchmark** — "
            f"€{listing.price_per_sqm:,.0f}/m² vs €{deal.benchmark.reference_price_per_sqm:,.0f}/m² "
            f"(fair value ≈ {_money(deal.expected_price_eur)}, "
            f"gap {_money(deal.saving_eur)})"
        )
        lines.append(f"- Source: {listing.source} — {listing.url}")
        if deal.flags:
            lines.append(f"- ⚠️ {'; '.join(deal.flags)}")
        lines.append("")

    lines.append(
        "_Benchmarks are asking prices, not registered sale prices. "
        "Always verify title deeds, shared ownership, covered vs. total area, "
        "and communal charges before making an offer._"
    )
    return "\n".join(lines)


def render_source_table(reports: dict[str, dict]) -> str:
    """Per-portal outcome, so a silent portal is visible instead of assumed empty."""
    if not reports:
        return ""

    lines = ["### Portal status", "", "| Portal | Pages ok | Listings | Extractor | Status |",
             "|---|---:|---:|---|---|"]
    for name, report in reports.items():
        extractors = report.get("extractors") or {}
        extractor = ", ".join(f"{k} ({v})" for k, v in extractors.items()) or "—"
        if report.get("blocked"):
            status = "🔴 blocked — needs `--engine browser`"
        elif report.get("unreachable"):
            status = "🔴 network unreachable — check connectivity/proxy"
        elif report.get("errors") and not report.get("listings"):
            status = "🔴 " + _first_error(report)
        elif not report.get("listings"):
            status = "🟠 reachable but nothing parsed — selectors need updating"
        else:
            status = "🟢 ok"
        lines.append(
            f"| {name} | {report.get('urls_ok', 0)}/{report.get('urls_tried', 0)} "
            f"| {report.get('listings', 0)} | {extractor} | {status} |"
        )
    return "\n".join(lines)


def _first_error(report: dict) -> str:
    errors = report.get("errors") or []
    if not errors:
        return "failed"
    text = str(errors[0])
    return (text[:110] + "…") if len(text) > 110 else text


def render_diagnostics(reports: dict[str, dict], listings: Sequence) -> str:
    """A fuller picture for --diagnose, including what parsed and what did not."""
    lines = ["## Portal diagnostics", "", render_source_table(reports), ""]

    by_source: dict[str, list] = {}
    for listing in listings:
        by_source.setdefault(listing.source, []).append(listing)

    for name, report in reports.items():
        found = by_source.get(name, [])
        lines.append(f"### {name}")
        if found:
            sample = found[0]
            missing = [
                field
                for field in ("price_eur", "area_sqm", "bedrooms", "lat")
                if getattr(sample, field) is None
            ]
            with_size = sum(1 for l in found if l.area_sqm)
            lines.append(
                f"- {len(found)} parsed, {with_size} with a usable size "
                f"({len(found) - with_size} would be dropped — try `--enrich-details 25`)"
            )
            lines.append(f"- Sample: {sample.title[:70]!r} — {sample.price_eur} — {sample.url}")
            if missing:
                lines.append(f"- Missing on the sample: {', '.join(missing)}")
        else:
            lines.append("- nothing parsed")
            for error in (report.get("errors") or [])[:2]:
                lines.append(f"- {_first_error({'errors': [error]})}")
            if report.get("blocked"):
                fix = (
                    "the portal refused an automated request — rerun with "
                    "`--engine browser`"
                )
            elif report.get("unreachable"):
                fix = (
                    "the host could not be reached at all — this is connectivity "
                    "or a proxy, not the scraper"
                )
            elif report.get("urls_ok"):
                fix = (
                    "the page loaded but no card matched — open a search URL in a "
                    f"browser, find the repeating result element, and update `{name}`'s "
                    "selectors in `larnaca_agent/scrapers/portals.py`"
                )
            else:
                fix = "no page loaded; check the search URLs for this portal"
            lines.append(f"- Fix: {fix}.")
        lines.append("")
    return "\n".join(lines)


def render_json(deals: Sequence[Deal], summary: dict) -> str:
    return json.dumps(
        {"summary": summary, "deals": [deal.to_dict() for deal in deals]},
        indent=2,
        ensure_ascii=False,
    )


def render_csv(deals: Sequence[Deal]) -> str:
    columns = [
        "discount_pct",
        "price_eur",
        "area_sqm",
        "price_per_sqm",
        "benchmark_eur_per_sqm",
        "expected_price_eur",
        "saving_eur",
        "bedrooms",
        "year_built",
        "area_key",
        "drive_minutes_to_coast",
        "source",
        "title",
        "url",
    ]
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
    writer.writeheader()
    for deal in deals:
        writer.writerow(deal.to_dict())
    return buffer.getvalue()
