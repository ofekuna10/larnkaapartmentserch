import datetime as dt

import pytest

from larnaca_agent.models import Listing
from larnaca_agent.normalize import (
    classify_resale,
    dedupe,
    is_plausible,
    match_area_by_text,
    parse_bedrooms,
    parse_price,
    parse_sqm,
    parse_year,
)


@pytest.mark.parametrize(
    "text,expected",
    [
        ("€ 185.000", 185000),
        ("185,000 EUR", 185000),
        ("€185 000", 185000),
        ("1.250.000", 1250000),
        ("249999", 249999),
        (185000.0, 185000.0),
        ("Price on application", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [
        ("85 m²", 85),
        ("85m2", 85),
        ("Covered area: 102 sq.m", 102),
        ("120 τ.μ.", 120),
        ("78.5 sqm", 78.5),
        ("2 bedrooms", None),
    ],
)
def test_parse_sqm(text, expected):
    assert parse_sqm(text) == expected


@pytest.mark.parametrize(
    "text,expected",
    [("3 bedrooms", 3), ("Bedrooms: 2", 2), ("Studio apartment", 0), ("2 υ/δ", 2)],
)
def test_parse_bedrooms(text, expected):
    assert parse_bedrooms(text) == expected


def test_parse_year():
    assert parse_year("Built in 2006") == 2006
    assert parse_year("no year here") is None


def test_classify_resale_uses_age_over_marketing():
    old = Listing(source="t", url="u", title="Brand new renovated flat", year_built=2005)
    is_resale, reason = classify_resale(old)
    assert is_resale is True
    assert "2005" in reason


def test_classify_resale_rejects_off_plan():
    new = Listing(source="t", url="u", title="2-bed, under construction, delivery 2028")
    is_resale, reason = classify_resale(new)
    assert is_resale is False
    assert "under construction" in reason


def test_classify_resale_rejects_recent_build():
    this_year = dt.date.today().year
    fresh = Listing(source="t", url="u", title="Apartment", year_built=this_year)
    assert classify_resale(fresh)[0] is False


def test_classify_resale_defaults_to_keeping():
    unknown = Listing(source="t", url="u", title="2-bedroom apartment in Livadia")
    is_resale, reason = classify_resale(unknown)
    assert is_resale is True
    assert reason.startswith("no new-build signal")


def test_match_area_by_text():
    assert match_area_by_text("Mackenzie, Larnaca") == "mackenzie"
    assert match_area_by_text("Λιβάδια, Λάρνακα") == "livadia"
    assert match_area_by_text("Aradippou") is None


def test_is_plausible_bounds():
    assert not is_plausible(Listing(source="t", url="u", price_eur=1500))
    assert not is_plausible(Listing(source="t", url="u", price_eur=200000, area_sqm=5))
    assert is_plausible(Listing(source="t", url="u", price_eur=200000, area_sqm=85))


def test_dedupe_merges_cross_portal_duplicates():
    a = Listing(
        source="bazaraki", url="https://a/1", price_eur=185000, area_sqm=84,
        bedrooms=2, location_text="Finikoudes",
    )
    b = Listing(
        source="index.cy", url="https://b/1", price_eur=185400, area_sqm=84,
        bedrooms=2, location_text="Finikoudes", year_built=1999, lat=34.91, lon=33.63,
    )
    merged = dedupe([a, b])
    assert len(merged) == 1
    # The richer record wins and keeps the extra fields.
    assert merged[0].year_built == 1999
    assert merged[0].lat == 34.91


def test_dedupe_keeps_distinct_listings():
    a = Listing(source="s", url="https://a/1", price_eur=185000, area_sqm=84, bedrooms=2)
    b = Listing(source="s", url="https://a/2", price_eur=310000, area_sqm=120, bedrooms=3)
    assert len(dedupe([a, b])) == 2
