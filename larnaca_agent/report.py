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
