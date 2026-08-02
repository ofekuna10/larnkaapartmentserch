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

    # Bazaraki puts the neighbourhood in the path, with its own slugs.
    AREA_PATHS = {
        "finikoudes": "/real-estate-for-sale/apartments-flats/larnaka-finikoudes/",
        "mackenzie": "/real-estate-for-sale/apartments-flats/larnaka-makenzy/",
        "livadia": "/real-estate-for-sale/apartments-flats/livadia-larnakas/",
    }
    # Central quarters that sit inside the coastal ring but are advertised under
    # their own slug; the geo stage decides which target area they fall into.
    NEIGHBOURING_PATHS = (
        "/real-estate-for-sale/apartments-flats/larnaka-chrysopolitissa/",
        "/real-estate-for-sale/apartments-flats/larnaka-harbor/",
        "/real-estate-for-sale/apartments-flats/larnaka-skala/",
    )
    FALLBACK_PATH = "/real-estate-for-sale/apartments-flats/larnaka-district-larnaca/"
    QUERY = "?ordering=newest"

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        urls = [
            f"{self.base_url}{self.AREA_PATHS[key]}{self.QUERY}"
            for key in area_keys
            if key in self.AREA_PATHS
        ]
        urls += [f"{self.base_url}{path}{self.QUERY}" for path in self.NEIGHBOURING_PATHS]
        # District-wide sweep catches ads that do not name the neighbourhood.
        urls.append(f"{self.base_url}{self.FALLBACK_PATH}{self.QUERY}")
        return urls

    def area_hint(self, url: str) -> Optional[str]:
        for key, path in self.AREA_PATHS.items():
            if path in url:
                return key
        return None


class IndexCyScraper(BaseScraper):
    """index.cy — large aggregator, agency and developer stock."""

    name = "index.cy"
    base_url = "https://index.cy"
    selectors = CardSelectors(
        card=(
            "article",
            "div[class*=listing-item]",
            "div.property-card",
            "div[class*=card]",
        ),
        link=("a[href^='/sale/']", "a[href*='/sale/']", "a[href]"),
        title=("h2", "h3", ".property-title", "[class*=title]"),
        price=("[class*=price]", ".price", "[class*=amount]"),
        area=("[class*=covered]", "[class*=area]", "[class*=sqm]", "[class*=size]"),
        bedrooms=("[class*=bedroom]", "[class*=bed]", "[class*=room]"),
        location=("[class*=location]", "[class*=address]", "[class*=district]"),
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
        card=("article", "div[class*=card]", "div[class*=property]", "div[class*=item]"),
        link=("a[href*='-for-sale/']", "a[href*='/property']", "a[href]"),
        title=("h2", "h3", ".title", "[class*=title]"),
        price=("[class*=price]", ".price"),
        area=("[class*=area]", "[class*=sqm]", "[class*=size]"),
        bedrooms=("[class*=bed]", "[class*=room]"),
        location=("[class*=location]", "[class*=area-name]", "[class*=district]"),
    )

    AREA_PATHS = {
        "finikoudes": "/apartment-for-sale/finikoudes/",
        "mackenzie": "/apartment-for-sale/mackenzie/",
        "livadia": "/apartment-for-sale/livadia/",
    }
    FALLBACK_PATH = "/property-for-sale/larnaca/"

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
        card=("div.item.standard", "div[class*=announcement]", "div[class*=listing]", "article"),
        link=("a.whole", "a[href*='/real-estate-for-sale/']", "a[href]"),
        title=("[class*=title]", "h2", "h3", ".name"),
        price=(".price", "[class*=price]"),
        area=("[class*=area]", "[class*=sqm]", ".specs"),
        bedrooms=("[class*=bed]", ".specs", "[class*=room]"),
        location=(".location", "[class*=location]", "[class*=region]"),
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


class ZyprusScraper(BaseScraper):
    """zyprus.com — searchable by neighbourhood through a query parameter."""

    name = "zyprus"
    base_url = "https://www.zyprus.com"
    selectors = CardSelectors(
        card=("div[class*=property-card]", "div[class*=listing]", "article"),
        link=("a[href*='/property/']", "a[href]"),
        title=("h2", "h3", "[class*=title]"),
        price=("[class*=price]",),
        area=("[class*=covered]", "[class*=area]", "[class*=sqm]"),
        bedrooms=("[class*=bed]",),
        location=("[class*=location]", "[class*=address]"),
    )

    # type_top[]=1 restricts the search to apartments.
    SEARCH_PATH = "/search/sale/grid?location={area}%2C+Larnaca&type_top%5B%5D=1"
    AREA_TERMS = {
        "finikoudes": "Finikoudes",
        "mackenzie": "Mackenzie",
        "livadia": "Livadia",
    }

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        return [
            self.base_url + self.SEARCH_PATH.format(area=self.AREA_TERMS[key])
            for key in area_keys
            if key in self.AREA_TERMS
        ]

    def area_hint(self, url: str) -> Optional[str]:
        for key, term in self.AREA_TERMS.items():
            if f"location={term}" in url:
                return key
        return None


class OfferCyScraper(BaseScraper):
    """offer.com.cy — per-neighbourhood apartment pages."""

    name = "offer.com.cy"
    base_url = "https://www.offer.com.cy"
    selectors = CardSelectors(
        card=("div[class*=property]", "div[class*=listing]", "article"),
        link=("a[href*='/en/']", "a[href]"),
        title=("h2", "h3", "[class*=title]"),
        price=("[class*=price]",),
        area=("[class*=area]", "[class*=sqm]"),
        bedrooms=("[class*=bed]",),
        location=("[class*=location]",),
    )

    AREA_PATHS = {
        "finikoudes": "/en/apartments/for-sale/larnaca--finikoudes/",
        "mackenzie": "/en/apartments/for-sale/larnaca--mackenzie/",
        "livadia": "/en/apartments/for-sale/larnaca--livadia/",
    }
    FALLBACK_PATH = "/en/apartments/for-sale/larnaca/"

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


ALL_SCRAPERS: tuple[type[BaseScraper], ...] = (
    BazarakiScraper,
    IndexCyScraper,
    ScalaScraper,
    HomeCyScraper,
    DomCyScraper,
    BuySellCyprusScraper,
    ZyprusScraper,
    OfferCyScraper,
)
