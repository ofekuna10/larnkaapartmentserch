"""Market benchmarking and deal detection.

The benchmark is the **median price per square metre** of comparable listings in
the same area, computed after trimming the tails. Median rather than mean
because a handful of penthouses (or the bargains we are hunting) would otherwise
drag the reference and hide the very listings we want.

A listing is a deal when its own €/m² is at least ``min_discount`` below that
benchmark.
"""

from __future__ import annotations

import statistics
from typing import Iterable, Optional, Sequence

from .config import THRESHOLDS, Thresholds
from .models import AreaBenchmark, Deal, Listing

COMBINED_KEY = "__combined__"


def _quantile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated quantile; ``statistics.quantiles`` needs n >= 2."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = q * (len(ordered) - 1)
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _trimmed(values: Sequence[float], quantile: float) -> list[float]:
    """Drop the outer ``quantile`` of the distribution at both ends."""
    if len(values) < 5 or quantile <= 0:
        return list(values)
    low = _quantile(values, quantile)
    high = _quantile(values, 1 - quantile)
    kept = [v for v in values if low <= v <= high]
    return kept or list(values)


def build_benchmarks(
    listings: Iterable[Listing],
    thresholds: Thresholds = THRESHOLDS,
) -> dict[str, AreaBenchmark]:
    """Compute a price benchmark per area, plus a combined fallback.

    Areas with too few listings borrow the combined benchmark so that a thin
    area still produces usable (clearly flagged) results.
    """
    listings = [l for l in listings if l.price_per_sqm and l.area_key]
    by_area: dict[str, list[Listing]] = {}
    for listing in listings:
        by_area.setdefault(listing.area_key, []).append(listing)

    benchmarks: dict[str, AreaBenchmark] = {}

    combined = _benchmark_from(COMBINED_KEY, listings, thresholds)
    if combined:
        benchmarks[COMBINED_KEY] = combined

    for area_key, group in by_area.items():
        benchmark = _benchmark_from(area_key, group, thresholds)
        if benchmark and len(group) >= thresholds.hard_min_sample:
            benchmark.low_confidence = len(group) < thresholds.min_sample_for_benchmark
            benchmarks[area_key] = benchmark
        elif combined:
            borrowed = AreaBenchmark(
                area_key=area_key,
                sample_size=combined.sample_size,
                median_price_per_sqm=combined.median_price_per_sqm,
                trimmed_mean_price_per_sqm=combined.trimmed_mean_price_per_sqm,
                p25_price_per_sqm=combined.p25_price_per_sqm,
                p75_price_per_sqm=combined.p75_price_per_sqm,
                median_price=combined.median_price,
                low_confidence=True,
                borrowed_from="all target areas combined",
            )
            benchmarks[area_key] = borrowed
    return benchmarks


def _benchmark_from(
    area_key: str,
    group: Sequence[Listing],
    thresholds: Thresholds,
) -> Optional[AreaBenchmark]:
    per_sqm = [l.price_per_sqm for l in group if l.price_per_sqm]
    if not per_sqm:
        return None
    trimmed = _trimmed(per_sqm, thresholds.trim_quantile)
    prices = [l.price_eur for l in group if l.price_eur]
    return AreaBenchmark(
        area_key=area_key,
        sample_size=len(per_sqm),
        median_price_per_sqm=statistics.median(per_sqm),
        trimmed_mean_price_per_sqm=statistics.fmean(trimmed),
        p25_price_per_sqm=_quantile(per_sqm, 0.25),
        p75_price_per_sqm=_quantile(per_sqm, 0.75),
        median_price=statistics.median(prices) if prices else 0.0,
    )


def find_deals(
    listings: Iterable[Listing],
    benchmarks: dict[str, AreaBenchmark],
    thresholds: Thresholds = THRESHOLDS,
) -> list[Deal]:
    """Return listings at least ``min_discount`` below their area benchmark."""
    deals: list[Deal] = []
    for listing in listings:
        price_per_sqm = listing.price_per_sqm
        if not price_per_sqm or not listing.area_key:
            continue
        benchmark = benchmarks.get(listing.area_key)
        if benchmark is None:
            continue

        reference = benchmark.reference_price_per_sqm
        if reference <= 0:
            continue
        discount = (reference - price_per_sqm) / reference
        if discount < thresholds.min_discount:
            continue

        expected_price = reference * listing.area_sqm
        deals.append(
            Deal(
                listing=listing,
                benchmark=benchmark,
                discount=discount,
                expected_price_eur=expected_price,
                saving_eur=expected_price - listing.price_eur,
                flags=_flags(listing, benchmark, discount, thresholds),
            )
        )

    deals.sort(key=lambda deal: deal.discount, reverse=True)
    return deals


def _flags(
    listing: Listing,
    benchmark: AreaBenchmark,
    discount: float,
    thresholds: Thresholds,
) -> list[str]:
    """Caveats a human should check before getting excited."""
    flags: list[str] = []
    if benchmark.borrowed_from:
        flags.append(f"benchmark borrowed from {benchmark.borrowed_from}")
    elif benchmark.low_confidence:
        flags.append(f"small benchmark sample (n={benchmark.sample_size})")
    if discount > 0.45:
        flags.append("discount too good to be true — check for share-of-title, "
                     "no title deeds, leasehold or a wrong size in the advert")
    if listing.resale_reason.startswith("no new-build signal"):
        flags.append("resale status unverified")
    if listing.raw.get("drive_time_method", "").endswith("centroid"):
        flags.append("no coordinates — drive time from area centre")
    if listing.year_built is None:
        flags.append("year built unknown")
    if listing.bedrooms == 0:
        flags.append("studio — €/m² normally runs above the area median")
    if listing.area_sqm and listing.area_sqm > 200:
        flags.append("large unit — €/m² normally runs below the area median")
    return flags


def summarise(
    listings: Sequence[Listing],
    benchmarks: dict[str, AreaBenchmark],
) -> dict:
    """Small dict describing the market snapshot, for the report header."""
    by_area: dict[str, int] = {}
    for listing in listings:
        if listing.area_key:
            by_area[listing.area_key] = by_area.get(listing.area_key, 0) + 1
    return {
        "listings_considered": len(listings),
        "listings_by_area": by_area,
        "benchmarks": {
            key: {
                "median_eur_per_sqm": round(bm.median_price_per_sqm),
                "trimmed_mean_eur_per_sqm": round(bm.trimmed_mean_price_per_sqm),
                "p25_eur_per_sqm": round(bm.p25_price_per_sqm),
                "p75_eur_per_sqm": round(bm.p75_price_per_sqm),
                "median_price_eur": round(bm.median_price),
                "sample": bm.sample_size,
                "low_confidence": bm.low_confidence,
            }
            for key, bm in benchmarks.items()
        },
    }
