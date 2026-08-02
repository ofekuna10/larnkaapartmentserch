from bs4 import BeautifulSoup

from larnaca_agent.scrapers import REGISTRY
from larnaca_agent.scrapers.base import (
    BaseScraper,
    CardSelectors,
    _find_coordinates,
    detect_cards,
)
from larnaca_agent.scrapers.portals import BazarakiScraper, ScalaScraper, ZyprusScraper

AREAS = ("livadia", "finikoudes", "mackenzie")


class _Scraper(BaseScraper):
    """A portal whose declared selectors are deliberately wrong."""

    name = "test"
    selectors = CardSelectors(card=("div.does-not-exist",))

    def search_urls(self, area_keys):
        return []


def _scraper():
    return _Scraper(fetcher=None)


UNKNOWN_MARKUP = """
<html><body><main>
  <section class="xy7">
    <a href="/adv/1_flat/">Nice 2-bedroom in Mackenzie</a>
    <span>€ 210.000</span><span>85 m²</span><span>2 bedrooms</span>
  </section>
  <section class="xy7">
    <a href="/adv/2_flat/">3-bedroom in Livadia</a>
    <span>€ 245.500</span><span>110 m²</span><span>3 bedrooms</span>
  </section>
  <section class="xy7">
    <a href="/adv/3_flat/">1-bedroom in Finikoudes</a>
    <span>€ 145.000</span><span>52 m²</span><span>1 bedroom</span>
  </section>
</main></body></html>
"""


def test_detect_cards_finds_repeated_blocks_in_unknown_markup():
    cards = detect_cards(BeautifulSoup(UNKNOWN_MARKUP, "lxml"))
    assert len(cards) == 3


def test_detect_cards_ignores_pages_without_repetition():
    html = "<html><body><div><a href='/x'>only one</a><span>€ 210.000</span></div></body></html>"
    assert detect_cards(BeautifulSoup(html, "lxml")) == []


def test_scraper_recovers_when_declared_selectors_miss():
    soup = BeautifulSoup(UNKNOWN_MARKUP, "lxml")
    listings = list(_scraper().parse_results(soup, "https://portal.test/search"))

    assert len(listings) == 3
    assert all(l.raw["extractor"] == "css-auto" for l in listings)

    first = listings[0]
    assert first.url == "https://portal.test/adv/1_flat/"
    assert first.price_eur == 210000
    assert first.area_sqm == 85
    assert first.bedrooms == 2


JSON_LD_MARKUP = """
<html><body><script type="application/ld+json">
{"@type": "Product", "name": "2-bed flat", "url": "/adv/9_flat/",
 "offers": {"@type": "Offer", "price": "199000", "priceCurrency": "EUR"},
 "floorSize": {"value": "88"}, "numberOfBedrooms": 2,
 "address": {"addressLocality": "Mackenzie"},
 "geo": {"latitude": 34.8865, "longitude": 33.6255}}
</script></body></html>
"""


def test_json_ld_is_preferred_and_complete():
    soup = BeautifulSoup(JSON_LD_MARKUP, "lxml")
    listings = list(_scraper().parse_results(soup, "https://portal.test/search"))

    assert len(listings) == 1
    listing = listings[0]
    assert listing.raw["extractor"] == "json-ld"
    assert listing.price_eur == 199000
    assert listing.area_sqm == 88
    assert listing.location_text == "Mackenzie"
    assert (listing.lat, listing.lon) == (34.8865, 33.6255)


def test_next_page_detection():
    html = "<html><body><a rel='next' href='/search?page=2'>next</a></body></html>"
    soup = BeautifulSoup(html, "lxml")
    assert _scraper().next_page_url(soup, "https://portal.test/search") == (
        "https://portal.test/search?page=2"
    )


def test_find_coordinates_from_various_shapes():
    assert _find_coordinates('{"latitude": 34.9182, "longitude": 33.6410}') == (34.9182, 33.6410)
    assert _find_coordinates('data-lat="34.8865" data-lng="33.6255"') == (34.8865, 33.6255)
    assert _find_coordinates("maps?q=34.9455,33.6350") == (34.9455, 33.6350)


def test_find_coordinates_rejects_points_outside_cyprus():
    assert _find_coordinates('{"latitude": 51.5074, "longitude": -0.1278}') is None


def test_find_coordinates_returns_none_when_absent():
    assert _find_coordinates("<html>no map here</html>") is None


# ------------------------------------------------------------ URL construction


def test_bazaraki_uses_the_real_area_slugs():
    urls = BazarakiScraper(fetcher=None).search_urls(AREAS)
    joined = " ".join(urls)
    assert "/apartments-flats/larnaka-makenzy/" in joined
    assert "/apartments-flats/larnaka-finikoudes/" in joined
    assert "/apartments-flats/livadia-larnakas/" in joined
    # District-wide sweep is always included as a backstop.
    assert "/apartments-flats/larnaka-district-larnaca/" in joined


def test_bazaraki_area_hint_matches_its_own_urls():
    scraper = BazarakiScraper(fetcher=None)
    for url in scraper.search_urls(AREAS):
        hint = scraper.area_hint(url)
        assert hint in {None, *AREAS}
    hinted = [scraper.area_hint(u) for u in scraper.search_urls(AREAS)]
    assert set(AREAS) <= set(hinted)


def test_every_portal_builds_urls_and_hints_consistently():
    for name, scraper_cls in REGISTRY.items():
        scraper = scraper_cls(fetcher=None)
        urls = scraper.search_urls(AREAS)
        assert urls, f"{name} produced no search URLs"
        for url in urls:
            assert url.startswith("https://"), f"{name} built a relative URL: {url}"
            assert scraper.area_hint(url) in {None, *AREAS}


def test_zyprus_encodes_the_area_query():
    urls = ZyprusScraper(fetcher=None).search_urls(["mackenzie"])
    assert urls == ["https://www.zyprus.com/search/sale/grid?location=Mackenzie%2C+Larnaca&type_top%5B%5D=1"]
    assert ZyprusScraper(fetcher=None).area_hint(urls[0]) == "mackenzie"


def test_scala_falls_back_to_the_district_page():
    urls = ScalaScraper(fetcher=None).search_urls(AREAS)
    assert urls[-1] == "https://www.scala.cy/property-for-sale/larnaca/"
