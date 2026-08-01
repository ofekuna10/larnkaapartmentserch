"""Scraper base class and portal-agnostic extraction helpers.

Portals redesign their markup regularly, so extraction is layered from most to
least durable:

1. **JSON-LD** (``schema.org`` ``Product`` / ``Offer`` / ``ItemList``) — most of
   these sites emit it for SEO and it survives redesigns.
2. **Embedded app state** (``__NEXT_DATA__``, ``window.__NUXT__``) — structured
   and stable while the framework stays the same.
3. **CSS selectors** — each scraper declares several candidates per field and
   the first that matches wins.

A scraper only has to describe its URLs and its selector candidates; the
traversal, parsing and error handling live here.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Iterator, Optional
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..fetcher import FetchError, Fetcher
from ..models import Listing
from ..normalize import parse_bedrooms, parse_price, parse_sqm, parse_year

log = logging.getLogger(__name__)

_JSON_LD_TYPES = {
    "product",
    "offer",
    "apartment",
    "residence",
    "singlefamilyresidence",
    "realestatelisting",
    "accommodation",
    "house",
}


@dataclass
class CardSelectors:
    """CSS selector candidates for one portal's search-results page."""

    card: tuple[str, ...] = ()
    link: tuple[str, ...] = ("a[href]",)
    title: tuple[str, ...] = ("h2", "h3", ".title")
    price: tuple[str, ...] = (".price", "[class*=price]")
    area: tuple[str, ...] = ("[class*=area]", "[class*=sqm]", "[class*=size]")
    bedrooms: tuple[str, ...] = ("[class*=bed]", "[class*=room]")
    location: tuple[str, ...] = ("[class*=location]", "[class*=address]", "[class*=district]")
    next_page: tuple[str, ...] = ("a[rel=next]", ".pagination a.next", "a.next")


class BaseScraper:
    """One portal. Subclasses provide ``name``, ``search_urls`` and selectors."""

    name: str = "base"
    base_url: str = ""
    selectors: CardSelectors = CardSelectors()
    # Portals that need a real browser to answer at all.
    requires_browser: bool = False

    def __init__(self, fetcher: Fetcher, max_pages: int = 3):
        self.fetcher = fetcher
        self.max_pages = max_pages

    # ------------------------------------------------------------- interface

    def search_urls(self, area_keys: Iterable[str]) -> list[str]:
        """Search-result URLs to crawl for the requested areas."""
        raise NotImplementedError

    def area_hint(self, url: str) -> Optional[str]:
        """Area implied by the URL, when the search itself is area-scoped."""
        return None

    def collect(self, area_keys: Iterable[str]) -> list[Listing]:
        """Crawl this portal and return everything it advertised."""
        listings: list[Listing] = []
        for url in self.search_urls(area_keys):
            hint = self.area_hint(url)
            page_url: Optional[str] = url
            for _ in range(self.max_pages):
                if not page_url:
                    break
                try:
                    html = self.fetcher.get(page_url)
                except FetchError as exc:
                    log.warning("[%s] %s", self.name, exc)
                    break

                soup = BeautifulSoup(html, "lxml")
                page_listings = list(self.parse_results(soup, page_url))
                for listing in page_listings:
                    if hint and not listing.location_text:
                        listing.location_text = hint
                    listing.raw.setdefault("area_hint", hint)
                listings.extend(page_listings)
                log.info(
                    "[%s] %s -> %d listings", self.name, page_url, len(page_listings)
                )
                if not page_listings:
                    break
                page_url = self.next_page_url(soup, page_url)
        return listings

    # ------------------------------------------------------------- extraction

    def parse_results(self, soup: BeautifulSoup, page_url: str) -> Iterator[Listing]:
        """Yield listings from a results page, best extractor first."""
        seen_urls: set[str] = set()

        for listing in self._from_json_ld(soup, page_url):
            if listing.url not in seen_urls:
                seen_urls.add(listing.url)
                yield listing

        for listing in self._from_app_state(soup, page_url):
            if listing.url not in seen_urls:
                seen_urls.add(listing.url)
                yield listing

        for listing in self._from_cards(soup, page_url):
            if listing.url not in seen_urls:
                seen_urls.add(listing.url)
                yield listing

    def next_page_url(self, soup: BeautifulSoup, page_url: str) -> Optional[str]:
        for selector in self.selectors.next_page:
            node = soup.select_one(selector)
            if node and node.get("href"):
                return urljoin(page_url, node["href"])
        return None

    # -------------------------------------------------------------- layer 1

    def _from_json_ld(self, soup: BeautifulSoup, page_url: str) -> Iterator[Listing]:
        for script in soup.find_all("script", type="application/ld+json"):
            payload = _load_json(script.string or script.get_text())
            if payload is None:
                continue
            for node in _walk_json(payload):
                listing = self._listing_from_json_ld(node, page_url)
                if listing:
                    yield listing

    def _listing_from_json_ld(
        self, node: dict[str, Any], page_url: str
    ) -> Optional[Listing]:
        node_type = node.get("@type")
        types = node_type if isinstance(node_type, list) else [node_type]
        if not any(str(t).lower() in _JSON_LD_TYPES for t in types if t):
            return None

        url = node.get("url") or node.get("@id")
        if not isinstance(url, str) or not url.strip():
            return None

        offers = node.get("offers")
        if isinstance(offers, list):
            offers = offers[0] if offers else {}
        offers = offers if isinstance(offers, dict) else {}

        price = parse_price(offers.get("price") or node.get("price"))
        listing = Listing(
            source=self.name,
            url=urljoin(page_url, url),
            title=_as_text(node.get("name")),
            price_eur=price,
            description=_as_text(node.get("description"))[:1500],
            raw={"extractor": "json-ld"},
        )
        listing.area_sqm = parse_sqm(
            _dig(node, "floorSize", "value") or node.get("floorSize")
        )
        listing.bedrooms = parse_bedrooms(
            node.get("numberOfBedrooms") or node.get("numberOfRooms")
        )
        listing.year_built = parse_year(node.get("yearBuilt"))
        listing.location_text = _address_text(node.get("address"))
        geo = node.get("geo") if isinstance(node.get("geo"), dict) else {}
        listing.lat = _as_float(geo.get("latitude"))
        listing.lon = _as_float(geo.get("longitude"))
        if listing.price_eur is None and listing.area_sqm is None:
            return None
        return listing

    # -------------------------------------------------------------- layer 2

    def _from_app_state(self, soup: BeautifulSoup, page_url: str) -> Iterator[Listing]:
        blobs: list[Any] = []

        next_data = soup.find("script", id="__NEXT_DATA__")
        if next_data:
            payload = _load_json(next_data.string or next_data.get_text())
            if payload is not None:
                blobs.append(payload)

        for script in soup.find_all("script"):
            text = script.string or ""
            match = re.search(r"window\.__(?:NUXT|INITIAL_STATE)__\s*=\s*(\{.*?\});?\s*$",
                              text.strip(), re.DOTALL)
            if match:
                payload = _load_json(match.group(1))
                if payload is not None:
                    blobs.append(payload)

        for blob in blobs:
            for node in _walk_json(blob):
                listing = self._listing_from_state(node, page_url)
                if listing:
                    yield listing

    def _listing_from_state(
        self, node: dict[str, Any], page_url: str
    ) -> Optional[Listing]:
        """Recognise an ad object inside a framework state blob."""
        keys = {k.lower() for k in node.keys()}
        if not ({"price"} & keys):
            return None
        url_key = next(
            (k for k in node if k.lower() in ("url", "link", "href", "slug", "permalink")),
            None,
        )
        if not url_key or not isinstance(node[url_key], str):
            return None
        price = parse_price(node.get(_key(node, "price")))
        if price is None:
            return None

        url = node[url_key]
        listing = Listing(
            source=self.name,
            url=urljoin(page_url, url if url.startswith(("http", "/")) else f"/{url}"),
            title=_as_text(node.get(_key(node, "title")) or node.get(_key(node, "name"))),
            price_eur=price,
            raw={"extractor": "app-state"},
        )
        for candidate in ("area", "sqm", "covered_area", "coveredarea", "size", "surface"):
            key = _key(node, candidate)
            if key:
                listing.area_sqm = parse_sqm(node[key])
                if listing.area_sqm:
                    break
        for candidate in ("bedrooms", "beds", "number_of_bedrooms", "rooms"):
            key = _key(node, candidate)
            if key:
                listing.bedrooms = parse_bedrooms(node[key])
                if listing.bedrooms is not None:
                    break
        for candidate in ("district", "location", "area_name", "city", "region", "address"):
            key = _key(node, candidate)
            if key and isinstance(node[key], str):
                listing.location_text = node[key]
                break
        listing.lat = _as_float(node.get(_key(node, "lat")) or node.get(_key(node, "latitude")))
        listing.lon = _as_float(node.get(_key(node, "lng")) or node.get(_key(node, "longitude")))
        listing.year_built = parse_year(
            node.get(_key(node, "year_built")) or node.get(_key(node, "construction_year"))
        )
        return listing

    # -------------------------------------------------------------- layer 3

    def _from_cards(self, soup: BeautifulSoup, page_url: str) -> Iterator[Listing]:
        cards = []
        for selector in self.selectors.card:
            cards = soup.select(selector)
            if cards:
                break
        for card in cards:
            listing = self._listing_from_card(card, page_url)
            if listing:
                yield listing

    def _listing_from_card(self, card, page_url: str) -> Optional[Listing]:
        href = None
        for selector in self.selectors.link:
            node = card.select_one(selector)
            if node and node.get("href"):
                href = node["href"]
                break
        if not href:
            return None

        text = card.get_text(" ", strip=True)
        listing = Listing(
            source=self.name,
            url=urljoin(page_url, href),
            title=_select_text(card, self.selectors.title) or text[:120],
            price_eur=parse_price(_select_text(card, self.selectors.price) or text),
            area_sqm=parse_sqm(_select_text(card, self.selectors.area) or text),
            bedrooms=parse_bedrooms(_select_text(card, self.selectors.bedrooms) or text),
            location_text=_select_text(card, self.selectors.location) or "",
            description=text[:1500],
            raw={"extractor": "css"},
        )
        return listing if listing.price_eur else None


# ---------------------------------------------------------------- utilities


def _key(node: dict[str, Any], name: str) -> Optional[str]:
    """Find a key case-insensitively, ignoring underscores."""
    target = name.replace("_", "").lower()
    for key in node:
        if key.replace("_", "").lower() == target:
            return key
    return None


def _load_json(text: Optional[str]) -> Any:
    if not text:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


def _walk_json(node: Any, depth: int = 0) -> Iterator[dict[str, Any]]:
    """Yield every dict in a nested JSON structure (depth-capped)."""
    if depth > 12:
        return
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk_json(value, depth + 1)
    elif isinstance(node, list):
        for value in node:
            yield from _walk_json(value, depth + 1)


def _select_text(card, selectors: Iterable[str]) -> str:
    for selector in selectors:
        node = card.select_one(selector)
        if node:
            text = node.get_text(" ", strip=True)
            if text:
                return text
    return ""


def _as_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)) and value:
        return _as_text(value[0])
    return ""


def _as_float(value: Any) -> Optional[float]:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result or None


def _address_text(address: Any) -> str:
    if isinstance(address, str):
        return address
    if isinstance(address, dict):
        parts = [
            address.get(key)
            for key in ("streetAddress", "addressLocality", "addressRegion")
        ]
        return ", ".join(str(p) for p in parts if p)
    return ""


def _dig(node: dict[str, Any], *path: str) -> Any:
    current: Any = node
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current
