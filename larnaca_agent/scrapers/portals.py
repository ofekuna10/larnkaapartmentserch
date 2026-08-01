"""Portal definitions.

Each class only declares *where* to look; all parsing lives in
:class:`~larnaca_agent.scrapers.base.BaseScraper`.

Search paths are kept in ``AREA_PATHS`` dictionaries so that a portal URL change
is a one-line fix. When a portal has no per-area page the district-wide search is
crawled instead and listings are placed by the geo stage.
"""

from __future__ import annotations

from typing import Iterable, Optional

from .base import BaseScraper, CardSelectors


class BazarakiScraper(BaseScraper):
    """bazaraki.com — the largest classifieds site in Cyprus, mostly private ads."""

    name = "bazaraki"
    base_url = "https://www.bazaraki.com"
    # Cloudflare rejects plain HTTP clients on most runs.
    requires_browser = True
    selectors = CardSelectors(
        card=("div.advert", "li.announcement-container", "article.advert"),
        link=("a.advert__content-title", "a[href*='/adv/']", "a[href]"),
        title=("a.advert__content-title", ".advert__content-title", "h2"),
        price=("meta[itemprop=price]", ".advert__content-price", "[class*=price]"),
        area=(".advert__content-attrs", "[class*=area]"),
        bedrooms=(".advert__content-attrs", "[class*=bed]"),
        location=(".advert__content-region", "[class*=region]", "[class*=location]"),
        next_page=("a.number-list-next", "a[rel=next]", ".pagination a.next"),
    )

    SEARCH_PATH = "/real-estate-for-sale/apartments-flats/district-larnaca/"
    # Bazaraki exposes neighbourhoods as a query filter rather than a path.
    AREA_QUERY = {
        "finikoudes": "Finikoudes",
        "mackenzie": "Mackenzie",
        "livadia": "Livadia",
    }

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        urls = []
        for key in area_keys:
            term = self.AREA_QUERY.get(key)
            if not term:
                continue
            urls.append(
                f"{self.base_url}{self.SEARCH_PATH}?q={term}&ordering=newest"
            )
        # District-wide sweep catches ads that do not name the neighbourhood.
        urls.append(f"{self.base_url}{self.SEARCH_PATH}?ordering=newest")
        return urls

    def area_hint(self, url: str) -> Optional[str]:
        for key, term in self.AREA_QUERY.items():
            if f"q={term}" in url:
                return key
        return None


class IndexCyScraper(BaseScraper):
    """index.cy — large aggregator, agency and developer stock."""

    name = "index.cy"
    base_url = "https://index.cy"
    selectors = CardSelectors(
        card=("div.property-card", "article.property", "div[class*=listing-item]"),
        link=("a[href*='/property/']", "a[href]"),
        title=("h2", "h3", ".property-title"),
        price=(".property-price", "[class*=price]"),
        area=("[class*=covered]", "[class*=area]", "[class*=sqm]"),
        bedrooms=("[class*=bedroom]", "[class*=bed]"),
        location=("[class*=location]", "[class*=address]"),
    )

    AREA_PATHS = {
        "finikoudes": "/for-sale/apartments-flats/larnaca/finikoudes/",
        "mackenzie": "/for-sale/apartments-flats/larnaca/mackenzie/",
        "livadia": "/for-sale/apartments-flats/larnaca/livadia/",
    }
    FALLBACK_PATH = "/for-sale/apartments-flats/larnaca/"

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        urls = [
            f"{self.base_url}{self.AREA_PATHS[key]}"
            for key in area_keys
            if key in self.AREA_PATHS
        ]
        urls.append(f"{self.base_url}{self.FALLBACK_PATH}")
        return urls

    def area_hint(self, url: str) -> Optional[str]:
        for key, path in self.AREA_PATHS.items():
            if path in url:
                return key
        return None


class ScalaScraper(BaseScraper):
    """scala.cy — agency-backed portal with good Larnaca coverage."""

    name = "scala.cy"
    base_url = "https://www.scala.cy"
    selectors = CardSelectors(
        card=("div.property-item", "article", "div[class*=card]"),
        link=("a[href*='-for-sale/']", "a[href]"),
        title=("h2", "h3", ".title"),
        price=("[class*=price]",),
        area=("[class*=area]", "[class*=sqm]", "[class*=size]"),
        bedrooms=("[class*=bed]",),
        location=("[class*=location]", "[class*=area-name]"),
    )

    AREA_PATHS = {
        "finikoudes": "/apartment-for-sale/finikoudes/",
        "mackenzie": "/apartment-for-sale/mackenzie/",
        "livadia": "/apartment-for-sale/livadia/",
    }
    FALLBACK_PATH = "/apartment-for-sale/larnaca/"

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        urls = [
            f"{self.base_url}{self.AREA_PATHS[key]}"
            for key in area_keys
            if key in self.AREA_PATHS
        ]
        urls.append(f"{self.base_url}{self.FALLBACK_PATH}")
        return urls

    def area_hint(self, url: str) -> Optional[str]:
        for key, path in self.AREA_PATHS.items():
            if path in url:
                return key
        return None


class HomeCyScraper(BaseScraper):
    """home.cy — Cyprus-wide portal (the site usually meant by "home.mc")."""

    name = "home.cy"
    base_url = "https://home.cy"
    requires_browser = True
    selectors = CardSelectors(
        card=("div[class*=announcement]", "div[class*=listing]", "article"),
        link=("a[href*='/adv']", "a[href*='/property']", "a[href]"),
        title=("h2", "h3", "[class*=title]"),
        price=("[class*=price]",),
        area=("[class*=area]", "[class*=sqm]"),
        bedrooms=("[class*=bed]",),
        location=("[class*=location]", "[class*=region]"),
    )

    SEARCH_PATH = "/real-estate-for-sale/apartments/larnaca"

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        urls = [
            f"{self.base_url}{self.SEARCH_PATH}?q={key.capitalize()}"
            for key in area_keys
        ]
        urls.append(f"{self.base_url}{self.SEARCH_PATH}")
        return urls

    def area_hint(self, url: str) -> Optional[str]:
        for key in ("finikoudes", "mackenzie", "livadia"):
            if f"q={key.capitalize()}" in url:
                return key
        return None


class DomCyScraper(BaseScraper):
    """dom.com.cy — additional portal, strong on per-area price statistics."""

    name = "dom.com.cy"
    base_url = "https://dom.com.cy"
    selectors = CardSelectors(
        card=("div[class*=object-card]", "div[class*=catalog-item]", "article"),
        link=("a[href*='/en/']", "a[href]"),
        title=("h3", "h2", "[class*=title]"),
        price=("[class*=price]",),
        area=("[class*=square]", "[class*=area]"),
        bedrooms=("[class*=bed]", "[class*=room]"),
        location=("[class*=address]", "[class*=location]"),
    )

    AREA_PATHS = {
        "finikoudes": "/en/catalog/sale/city-larnaca/area-finikoudes/type-apartment/",
        "mackenzie": "/en/catalog/sale/city-larnaca/area-mackenzie/type-apartment/",
        "livadia": "/en/catalog/sale/city-larnaca/area-livadia/type-apartment/",
    }
    FALLBACK_PATH = "/en/catalog/sale/city-larnaca/type-apartment/"

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        urls = [
            f"{self.base_url}{self.AREA_PATHS[key]}"
            for key in area_keys
            if key in self.AREA_PATHS
        ]
        urls.append(f"{self.base_url}{self.FALLBACK_PATH}")
        return urls

    def area_hint(self, url: str) -> Optional[str]:
        for key, path in self.AREA_PATHS.items():
            if path in url:
                return key
        return None


class BuySellCyprusScraper(BaseScraper):
    """buysellcyprus.com — agency network, often lists motivated resales."""

    name = "buysellcyprus"
    base_url = "https://www.buysellcyprus.com"
    selectors = CardSelectors(
        card=("div.property-listing", "div[class*=property-item]", "article"),
        link=("a[href*='/properties/']", "a[href]"),
        title=("h2", "h3", "[class*=title]"),
        price=("[class*=price]",),
        area=("[class*=covered]", "[class*=area]"),
        bedrooms=("[class*=bed]",),
        location=("[class*=location]",),
    )

    SEARCH_PATH = "/properties-for-sale/apartments/larnaca-district"

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        return [f"{self.base_url}{self.SEARCH_PATH}"]


ALL_SCRAPERS: tuple[type[BaseScraper], ...] = (
    BazarakiScraper,
    IndexCyScraper,
    ScalaScraper,
    HomeCyScraper,
    DomCyScraper,
    BuySellCyprusScraper,
)
