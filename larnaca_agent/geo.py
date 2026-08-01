"""Geography: area assignment and drive time to the Larnaca coastline.

Drive time is resolved in two ways:

1. OSRM (the public demo router, or a self-hosted one via ``--osrm-url``) gives
   a real road-network duration.
2. When OSRM is unreachable, a straight-line estimate is used: crow-flight
   distance x a detour factor, at an urban average speed. In central Larnaca
   this tracks the routed value closely enough for a 10-minute cut-off.
"""

from __future__ import annotations

import json
import math
import urllib.parse
from typing import Optional

from .config import AREAS, COASTLINE_POINTS
from .models import Listing

EARTH_RADIUS_KM = 6371.0

# Street layout is a grid-ish sprawl; routed distance runs ~30% over crow-flight.
DETOUR_FACTOR = 1.35
# Average door-to-beach speed in town, including junctions and parking search.
URBAN_SPEED_KMH = 27.0

DEFAULT_OSRM_URL = "https://router.project-osrm.org"


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in kilometres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlambda = math.radians(lon2 - lon1)
    a = (
        math.sin(dphi / 2) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(dlambda / 2) ** 2
    )
    return 2 * EARTH_RADIUS_KM * math.asin(math.sqrt(a))


def estimate_drive_minutes(distance_km: float) -> float:
    """Convert crow-flight distance into an estimated driving time."""
    return (distance_km * DETOUR_FACTOR) / URBAN_SPEED_KMH * 60.0


def nearest_coast_point(lat: float, lon: float) -> tuple[float, float, float]:
    """Return (lat, lon, distance_km) of the closest coastline sample point."""
    best = min(
        COASTLINE_POINTS,
        key=lambda point: haversine_km(lat, lon, point[0], point[1]),
    )
    return best[0], best[1], haversine_km(lat, lon, best[0], best[1])


def area_for_coordinates(lat: float, lon: float) -> Optional[str]:
    """Assign coordinates to the nearest target area within its radius."""
    best_key, best_distance = None, math.inf
    for area in AREAS.values():
        distance = haversine_km(lat, lon, area.lat, area.lon)
        if distance <= area.radius_km and distance < best_distance:
            best_key, best_distance = area.key, distance
    return best_key


class DriveTimeResolver:
    """Drive time to the coast, routed when possible and estimated otherwise."""

    def __init__(self, osrm_url: str | None = DEFAULT_OSRM_URL, timeout: int = 15):
        self.osrm_url = (osrm_url or "").rstrip("/")
        self.timeout = timeout
        self._osrm_failed = False
        self._cache: dict[tuple[float, float], tuple[float, str]] = {}

    def minutes_to_coast(self, lat: float, lon: float) -> tuple[float, str]:
        """Return (minutes, method) where method is 'osrm' or 'estimate'."""
        key = (round(lat, 4), round(lon, 4))
        if key in self._cache:
            return self._cache[key]

        result: tuple[float, str] | None = None
        if self.osrm_url and not self._osrm_failed:
            routed = self._osrm_minutes(lat, lon)
            if routed is not None:
                result = (routed, "osrm")
            else:
                self._osrm_failed = True

        if result is None:
            _, _, distance_km = nearest_coast_point(lat, lon)
            result = (estimate_drive_minutes(distance_km), "estimate")

        self._cache[key] = result
        return result

    def _osrm_minutes(self, lat: float, lon: float) -> Optional[float]:
        """One OSRM /table call: origin -> every coastline point."""
        try:
            import requests
        except ImportError:  # pragma: no cover - requests is a hard dependency
            return None

        coordinates = ";".join(
            f"{lon:.6f},{lat:.6f}"
            for lat, lon in ((lat, lon), *COASTLINE_POINTS)
        )
        destinations = ";".join(str(i) for i in range(1, len(COASTLINE_POINTS) + 1))
        url = (
            f"{self.osrm_url}/table/v1/driving/{urllib.parse.quote(coordinates)}"
            f"?sources=0&destinations={destinations}&annotations=duration"
        )
        try:
            response = requests.get(url, timeout=self.timeout)
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return None

        if payload.get("code") != "Ok":
            return None
        durations = (payload.get("durations") or [[]])[0]
        values = [d for d in durations if isinstance(d, (int, float))]
        return min(values) / 60.0 if values else None


def enrich_location(
    listing: Listing,
    resolver: DriveTimeResolver,
    text_area: Optional[str] = None,
) -> Listing:
    """Fill in ``area_key`` and ``drive_minutes_to_coast`` on a listing.

    Coordinates win when the portal published them; otherwise the area is taken
    from the advert text and the area centroid stands in for the address.
    """
    from .normalize import match_area_by_text  # local import avoids a cycle

    area_key = None
    if listing.lat is not None and listing.lon is not None:
        area_key = area_for_coordinates(listing.lat, listing.lon)
    if area_key is None:
        area_key = text_area or match_area_by_text(
            f"{listing.location_text} {listing.title}"
        )
    listing.area_key = area_key

    if listing.lat is not None and listing.lon is not None:
        minutes, method = resolver.minutes_to_coast(listing.lat, listing.lon)
    elif area_key:
        area = AREAS[area_key]
        minutes, method = resolver.minutes_to_coast(area.lat, area.lon)
        method += "-centroid"
    else:
        return listing

    listing.drive_minutes_to_coast = round(minutes, 1)
    listing.raw["drive_time_method"] = method
    return listing
