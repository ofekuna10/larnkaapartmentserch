"""Static configuration: target areas, coastline geometry and deal thresholds.

Coordinates are hand-picked reference points (WGS84). They only need to be
accurate to ~100 m: they are used for area assignment and for a drive-time
estimate to the coast, not for navigation.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Area:
    """A target neighbourhood in central Larnaca."""

    key: str
    display_name: str
    lat: float
    lon: float
    # Radius used when assigning a listing to this area purely from coordinates.
    radius_km: float
    # Lowercase substrings that identify the area in a listing title/location
    # string. Includes Greek and common misspellings used by the portals.
    aliases: tuple[str, ...]


AREAS: dict[str, Area] = {
    "finikoudes": Area(
        key="finikoudes",
        display_name="Finikoudes / Larnaca centre",
        lat=34.9165,
        lon=33.6355,
        radius_km=1.2,
        aliases=(
            "finikoudes",
            "phinikoudes",
            "foinikoudes",
            "φοινικουδες",
            "φοινικούδες",
            "larnaca centre",
            "larnaca center",
            "town centre",
            "city centre",
            "athinon avenue",
            "athens avenue",
            "piale pasha",
            "marina",
        ),
    ),
    "mackenzie": Area(
        key="mackenzie",
        display_name="Mackenzie",
        lat=34.8865,
        lon=33.6255,
        radius_km=1.3,
        aliases=(
            "mackenzie",
            "mackenzy",
            "mckenzie",
            "makenzie",
            "makenzy",
            "μακενζυ",
            "μακένζυ",
        ),
    ),
    "livadia": Area(
        key="livadia",
        display_name="Livadia",
        lat=34.9455,
        lon=33.6350,
        radius_km=1.8,
        aliases=(
            "livadia",
            "livadhia",
            "λιβαδια",
            "λιβάδια",
        ),
    ),
}

DEFAULT_AREAS: tuple[str, ...] = ("livadia", "finikoudes", "mackenzie")

# Coastline sample points from Mackenzie (south, by the airport) up to
# Oroklini/Pyla (north). Drive time to "the coast" is the minimum drive time to
# any of these points.
COASTLINE_POINTS: tuple[tuple[float, float], ...] = (
    (34.8721, 33.6202),  # south Mackenzie, airport end
    (34.8829, 33.6272),  # Mackenzie beach
    (34.8952, 33.6339),  # Mackenzie north / Dhekelia road start
    (34.9075, 33.6383),  # Piale Pasha south
    (34.9182, 33.6410),  # Finikoudes beach
    (34.9280, 33.6445),  # Larnaca marina
    (34.9390, 33.6480),  # Livadia beach south
    (34.9470, 33.6510),  # Livadia beach
    (34.9580, 33.6560),  # Oroklini beach south
    (34.9700, 33.6660),  # Oroklini / CTO beach
)


@dataclass(frozen=True)
class Thresholds:
    """Tunable knobs for filtering and deal detection."""

    # A listing qualifies if it is within this many minutes' drive of the coast.
    max_drive_minutes_to_coast: float = 10.0
    # Minimum discount vs. the area benchmark to be reported as a deal.
    min_discount: float = 0.15
    # Sanity bounds — anything outside is a parsing error or not a real home.
    min_price_eur: float = 25_000
    max_price_eur: float = 3_000_000
    min_area_sqm: float = 20
    max_area_sqm: float = 500
    # Benchmarks computed from fewer listings than this are flagged low
    # confidence (still reported, but marked).
    min_sample_for_benchmark: int = 8
    # Below this the area benchmark is not trustworthy at all and the agent
    # falls back to the combined benchmark across all target areas.
    hard_min_sample: int = 4
    # Drop the cheapest/most expensive tail before averaging, so that a handful
    # of mispriced or luxury listings do not move the benchmark.
    trim_quantile: float = 0.10


THRESHOLDS = Thresholds()

# Second-hand only: a listing matching any of these is treated as new-build /
# off-plan and dropped.
NEW_BUILD_KEYWORDS: tuple[str, ...] = (
    "under construction",
    "off plan",
    "off-plan",
    "off plan project",
    "brand new",
    "new build",
    "newly built",
    "new project",
    "new development",
    "completion 20",
    "delivery 20",
    "ready 20",
    "pre-launch",
    "presale",
    "pre-sale",
    "υπό ανέγερση",
    "υπο ανεγερση",
    "νεόδμητο",
    "νεοδμητο",
    "καινούριο",
)

# Explicit resale signals — used to keep a listing when the condition field is
# ambiguous.
RESALE_KEYWORDS: tuple[str, ...] = (
    "resale",
    "second hand",
    "second-hand",
    "used",
    "renovated",
    "μεταπώληση",
    "μεταπωληση",
    "μεταχειρισμένο",
)

# A property completed at least this many years ago is considered second-hand
# even if the advert calls itself "new".
RESALE_MIN_AGE_YEARS = 2

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Seconds between requests to the same host.
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 30
