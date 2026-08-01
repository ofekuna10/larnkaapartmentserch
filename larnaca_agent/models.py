"""Core data structures shared by scrapers, analysis and reporting."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field, asdict
from typing import Any, Optional


@dataclass
class Listing:
    """One apartment advert, normalised across portals."""

    source: str
    url: str
    title: str = ""
    price_eur: Optional[float] = None
    area_sqm: Optional[float] = None
    bedrooms: Optional[int] = None
    bathrooms: Optional[int] = None
    floor: Optional[str] = None
    year_built: Optional[int] = None
    location_text: str = ""
    description: str = ""
    lat: Optional[float] = None
    lon: Optional[float] = None
    posted_at: Optional[str] = None

    # Filled in by the enrichment stage.
    area_key: Optional[str] = None
    drive_minutes_to_coast: Optional[float] = None
    is_resale: Optional[bool] = None
    resale_reason: str = ""

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def price_per_sqm(self) -> Optional[float]:
        if not self.price_eur or not self.area_sqm:
            return None
        return self.price_eur / self.area_sqm

    @property
    def listing_id(self) -> str:
        """Stable id used for de-duplication and the seen-listings store."""
        return hashlib.sha1(self.url.encode("utf-8")).hexdigest()[:16]

    def searchable_text(self) -> str:
        return " ".join(
            part.lower()
            for part in (self.title, self.location_text, self.description)
            if part
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["price_per_sqm"] = self.price_per_sqm
        data["listing_id"] = self.listing_id
        return data


@dataclass
class AreaBenchmark:
    """Market statistics for one area, used as the reference price."""

    area_key: str
    sample_size: int
    median_price_per_sqm: float
    trimmed_mean_price_per_sqm: float
    p25_price_per_sqm: float
    p75_price_per_sqm: float
    median_price: float
    low_confidence: bool = False
    borrowed_from: Optional[str] = None

    @property
    def reference_price_per_sqm(self) -> float:
        """The value deals are measured against.

        The median is used rather than the mean: it is unaffected by a few
        penthouses or by the very bargains we are trying to detect.
        """
        return self.median_price_per_sqm


@dataclass
class Deal:
    """A listing priced meaningfully below its area benchmark."""

    listing: Listing
    benchmark: AreaBenchmark
    discount: float  # 0.23 == 23% below the benchmark
    expected_price_eur: float
    saving_eur: float
    flags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discount_pct": round(self.discount * 100, 1),
            "expected_price_eur": round(self.expected_price_eur),
            "saving_eur": round(self.saving_eur),
            "benchmark_eur_per_sqm": round(self.benchmark.reference_price_per_sqm),
            "benchmark_sample": self.benchmark.sample_size,
            "flags": self.flags,
            **self.listing.to_dict(),
        }
