from pathlib import Path

import pytest

from larnaca_agent.config import Thresholds
from larnaca_agent.models import Listing
from larnaca_agent.pipeline import AgentConfig, analyse, load_listings
from larnaca_agent.report import render_csv, render_json, render_markdown

FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "sample_listings.json"


@pytest.fixture(scope="module")
def config():
    # osrm_url=None keeps the test offline and deterministic.
    return AgentConfig(osrm_url=None, cache_dir=None)


@pytest.fixture(scope="module")
def result(config):
    return analyse(load_listings(FIXTURE), config)


def test_fixture_loads(config):
    listings = load_listings(FIXTURE)
    assert len(listings) > 50
    assert all(isinstance(l, Listing) for l in listings)


def test_new_builds_are_dropped(result):
    assert result.stats["new_build"] >= 1
    assert all("under construction" not in l.title.lower() for l in result.kept)


def test_listings_outside_the_target_areas_are_dropped(result):
    assert result.stats["outside_area"] >= 1
    assert all(l.area_key in {"livadia", "finikoudes", "mackenzie"} for l in result.kept)


def test_listings_without_a_size_are_dropped(result):
    assert result.stats["no_size"] >= 1
    assert all(l.area_sqm for l in result.kept)


def test_every_kept_listing_is_within_the_drive_time_ring(result, config):
    limit = config.thresholds.max_drive_minutes_to_coast
    assert all(l.drive_minutes_to_coast <= limit for l in result.kept)


def test_deals_clear_the_threshold(result):
    assert result.deals, "the fixture is seeded with bargains"
    assert all(deal.discount >= 0.15 for deal in result.deals)


def test_deals_are_cheaper_per_sqm_than_their_benchmark(result):
    for deal in result.deals:
        assert deal.listing.price_per_sqm < deal.benchmark.reference_price_per_sqm
        assert deal.saving_eur > 0


def test_benchmarks_differ_between_areas(result):
    benchmarks = result.summary["benchmarks"]
    assert {"livadia", "finikoudes", "mackenzie"} <= set(benchmarks)
    # Livadia is inland and cheaper than the two beachfront areas.
    assert (
        benchmarks["livadia"]["median_eur_per_sqm"]
        < benchmarks["mackenzie"]["median_eur_per_sqm"]
    )


def test_stricter_threshold_yields_fewer_deals(config):
    listings = load_listings(FIXTURE)
    strict = AgentConfig(
        osrm_url=None, cache_dir=None, thresholds=Thresholds(min_discount=0.30)
    )
    assert len(analyse(listings, strict).deals) <= len(analyse(listings, config).deals)


def test_reports_render(result):
    markdown = render_markdown(result.deals, result.summary)
    assert "below the area benchmark" in markdown
    assert "€/m²" in markdown

    payload = render_json(result.deals, result.summary)
    assert '"deals"' in payload

    csv_text = render_csv(result.deals)
    assert csv_text.splitlines()[0].startswith("discount_pct")


def test_report_handles_no_deals(result):
    markdown = render_markdown([], result.summary)
    assert "No listing currently clears the 15% threshold." in markdown
