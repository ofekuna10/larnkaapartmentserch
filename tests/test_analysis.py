import pytest

from larnaca_agent.analysis import COMBINED_KEY, build_benchmarks, find_deals, summarise
from larnaca_agent.config import Thresholds
from larnaca_agent.models import Listing


def make(area_key, price_per_sqm, sqm=80, url=None, **kwargs):
    return Listing(
        source="test",
        url=url or f"https://x/{area_key}/{price_per_sqm}/{sqm}",
        price_eur=price_per_sqm * sqm,
        area_sqm=sqm,
        area_key=area_key,
        **kwargs,
    )


def uniform_area(area_key, price_per_sqm, count=12):
    return [make(area_key, price_per_sqm, url=f"https://x/{area_key}/{i}") for i in range(count)]


def test_benchmark_median_is_the_reference():
    listings = uniform_area("livadia", 2000, count=10)
    benchmarks = build_benchmarks(listings)
    assert benchmarks["livadia"].reference_price_per_sqm == 2000
    assert benchmarks["livadia"].sample_size == 10
    assert benchmarks["livadia"].low_confidence is False


def test_benchmark_resists_outliers():
    listings = uniform_area("livadia", 2000, count=12)
    listings.append(make("livadia", 9000, url="https://x/penthouse"))
    benchmarks = build_benchmarks(listings)
    assert benchmarks["livadia"].reference_price_per_sqm == 2000
    # The trimmed mean must not be dragged far by the single luxury unit.
    assert benchmarks["livadia"].trimmed_mean_price_per_sqm < 2200


def test_deal_threshold_is_exclusive_below_15_percent():
    listings = uniform_area("livadia", 2000, count=12)
    just_under = make("livadia", 1701, url="https://x/just-under")   # 14.95% off
    just_over = make("livadia", 1700, url="https://x/just-over")     # exactly 15% off
    listings += [just_under, just_over]

    benchmarks = build_benchmarks(listings)
    deals = find_deals(listings, benchmarks)
    urls = {deal.listing.url for deal in deals}

    assert "https://x/just-over" in urls
    assert "https://x/just-under" not in urls


def test_deal_reports_expected_price_and_saving():
    listings = uniform_area("mackenzie", 3000, count=12)
    bargain = make("mackenzie", 2100, sqm=100, url="https://x/bargain")
    listings.append(bargain)

    deals = find_deals(listings, build_benchmarks(listings))
    deal = next(d for d in deals if d.listing.url == "https://x/bargain")

    assert deal.discount == pytest.approx(0.30, abs=1e-6)
    assert deal.expected_price_eur == pytest.approx(300_000)
    assert deal.saving_eur == pytest.approx(90_000)


def test_deals_are_sorted_by_discount():
    listings = uniform_area("livadia", 2000, count=12)
    listings.append(make("livadia", 1600, url="https://x/a"))  # 20%
    listings.append(make("livadia", 1200, url="https://x/b"))  # 40%
    deals = find_deals(listings, build_benchmarks(listings))
    assert [d.listing.url for d in deals[:2]] == ["https://x/b", "https://x/a"]


def test_thin_area_borrows_combined_benchmark():
    listings = uniform_area("livadia", 2000, count=12)
    listings += [make("mackenzie", 2000, url="https://x/m1")]  # only one listing
    benchmarks = build_benchmarks(listings)
    assert benchmarks["mackenzie"].borrowed_from is not None
    assert benchmarks["mackenzie"].low_confidence is True
    assert COMBINED_KEY in benchmarks


def test_small_sample_is_flagged_but_used():
    thresholds = Thresholds(min_sample_for_benchmark=8, hard_min_sample=4)
    listings = uniform_area("livadia", 2000, count=5)
    benchmarks = build_benchmarks(listings, thresholds)
    assert benchmarks["livadia"].low_confidence is True
    assert benchmarks["livadia"].borrowed_from is None


def test_flags_warn_about_extreme_discounts():
    listings = uniform_area("livadia", 2000, count=12)
    listings.append(make("livadia", 900, url="https://x/suspicious"))  # 55% off
    deals = find_deals(listings, build_benchmarks(listings))
    deal = next(d for d in deals if d.listing.url == "https://x/suspicious")
    assert any("too good to be true" in flag for flag in deal.flags)


def test_listing_without_size_is_ignored():
    listings = uniform_area("livadia", 2000, count=12)
    no_size = Listing(source="t", url="https://x/nosize", price_eur=50_000,
                      area_key="livadia")
    listings.append(no_size)
    benchmarks = build_benchmarks(listings)
    assert benchmarks["livadia"].sample_size == 12
    assert all(d.listing.url != "https://x/nosize" for d in find_deals(listings, benchmarks))


def test_custom_discount_threshold():
    listings = uniform_area("livadia", 2000, count=12)
    listings.append(make("livadia", 1850, url="https://x/small-discount"))  # 7.5%
    thresholds = Thresholds(min_discount=0.05)
    deals = find_deals(listings, build_benchmarks(listings), thresholds)
    assert any(d.listing.url == "https://x/small-discount" for d in deals)


def test_summarise_shape():
    listings = uniform_area("livadia", 2000, count=10)
    summary = summarise(listings, build_benchmarks(listings))
    assert summary["listings_considered"] == 10
    assert summary["listings_by_area"]["livadia"] == 10
    assert summary["benchmarks"]["livadia"]["median_eur_per_sqm"] == 2000
