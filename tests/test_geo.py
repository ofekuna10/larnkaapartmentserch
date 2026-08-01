import pytest

from larnaca_agent.geo import (
    DriveTimeResolver,
    area_for_coordinates,
    estimate_drive_minutes,
    haversine_km,
    nearest_coast_point,
)
from larnaca_agent.models import Listing
from larnaca_agent import geo


def test_haversine_known_distance():
    # Finikoudes beach to Mackenzie beach is roughly 4 km along the shore.
    distance = haversine_km(34.9182, 33.6410, 34.8829, 33.6272)
    assert 3.5 < distance < 4.5


def test_nearest_coast_point_for_finikoudes():
    lat, lon, distance = nearest_coast_point(34.9165, 33.6355)
    assert distance < 1.0
    assert 34.90 < lat < 34.93


def test_estimate_drive_minutes_scales_with_distance():
    assert estimate_drive_minutes(0) == 0
    assert estimate_drive_minutes(2) < estimate_drive_minutes(5)
    # 2 km crow-flight in town should be a few minutes, not half an hour.
    assert 3 < estimate_drive_minutes(2) < 8


def test_area_for_coordinates():
    assert area_for_coordinates(34.9165, 33.6355) == "finikoudes"
    assert area_for_coordinates(34.8865, 33.6255) == "mackenzie"
    assert area_for_coordinates(34.9455, 33.6350) == "livadia"
    # Aradippou, well inland — outside every target radius.
    assert area_for_coordinates(34.9560, 33.5820) is None


def test_resolver_falls_back_to_estimate_without_osrm():
    resolver = DriveTimeResolver(osrm_url=None)
    minutes, method = resolver.minutes_to_coast(34.9165, 33.6355)
    assert method == "estimate"
    assert 0 <= minutes < 10


def test_resolver_caches_results(monkeypatch):
    resolver = DriveTimeResolver(osrm_url=None)
    calls = []
    original = geo.nearest_coast_point

    def counting(lat, lon):
        calls.append((lat, lon))
        return original(lat, lon)

    monkeypatch.setattr(geo, "nearest_coast_point", counting)
    resolver.minutes_to_coast(34.9165, 33.6355)
    resolver.minutes_to_coast(34.9165, 33.6355)
    assert len(calls) == 1


def test_enrich_location_uses_text_when_no_coordinates():
    resolver = DriveTimeResolver(osrm_url=None)
    listing = Listing(source="t", url="u", location_text="Livadia, Larnaca")
    geo.enrich_location(listing, resolver)
    assert listing.area_key == "livadia"
    assert listing.drive_minutes_to_coast is not None
    assert listing.raw["drive_time_method"].endswith("centroid")


def test_enrich_location_prefers_coordinates_over_text():
    resolver = DriveTimeResolver(osrm_url=None)
    listing = Listing(
        source="t", url="u", location_text="Livadia, Larnaca",
        lat=34.8865, lon=33.6255,
    )
    geo.enrich_location(listing, resolver)
    assert listing.area_key == "mackenzie"
