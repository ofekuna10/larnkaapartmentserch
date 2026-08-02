"""Parsing helpers that turn messy portal text into numbers and booleans."""

from __future__ import annotations

import datetime as _dt
import re
from typing import Iterable, Optional

from .config import (
    AREAS,
    NEW_BUILD_KEYWORDS,
    RESALE_KEYWORDS,
    RESALE_MIN_AGE_YEARS,
    THRESHOLDS,
)
from .models import Listing

# A price is either grouped ("210.000", "185 000", "185,000.50") or a plain
# run of digits. Anything looser swallows the next number on the card.
_NUMBER = r"\d{1,3}(?:[.,\s ]\d{3})+(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?"
_PRICE_RE = re.compile(rf"({_NUMBER})")
# A number tied to a currency marker is the price; a bare number inside a
# sentence is usually a bedroom count, a floor or a street number.
_CURRENCY_PRICE_RE = re.compile(
    rf"(?:\u20ac|EUR)\s*({_NUMBER})|({_NUMBER})\s*(?:\u20ac|EUR)",
    re.IGNORECASE,
)
_HAS_LETTERS_RE = re.compile(r"[^\W\d_]", re.UNICODE)
_SQM_RE = re.compile(
    r"(\d[\d.,]*)\s*(?:m²|m2|sq\.?\s*m|sqm|τ\.?μ\.?|τετρ)", re.IGNORECASE
)
_BEDROOM_RE = re.compile(
    r"(\d+)\s*(?:bed(?:room)?s?|υ/δ|υπν)|(?:bed(?:room)?s?)\s*[:\-]?\s*(\d+)",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b(19[5-9]\d|20[0-4]\d)\b")


def parse_price(value: object) -> Optional[float]:
    """Parse '€ 185.000', '185,000 EUR', 185000.0 -> 185000.0.

    Handles both European (185.000,50) and Anglo (185,000.50) grouping.
    """
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) or None

    text = str(value)
    if not text.strip():
        return None
    # "Price on application", "POA", "Negotiable"
    if not any(ch.isdigit() for ch in text):
        return None

    normalised = text.replace(" ", " ")

    # Prefer a number attached to a currency marker. Card text such as
    # "Nice 2-bedroom flat € 210.000 85 m²" would otherwise yield 2.
    currency_match = _CURRENCY_PRICE_RE.search(normalised)
    if currency_match:
        raw_number = currency_match.group(1) or currency_match.group(2)
    else:
        match = _PRICE_RE.search(normalised)
        if not match:
            return None
        raw_number = match.group(1)
        # No currency marker, and prose around the number: too ambiguous to
        # read as a price unless it is large enough to be one.
        if _HAS_LETTERS_RE.search(normalised):
            digits = re.sub(r"\D", "", raw_number)
            if len(digits) < 4:
                return None

    number = re.sub(r"[\s ]", "", raw_number)
    if "," in number and "." in number:
        # Whichever separator comes last is the decimal separator.
        decimal_sep = "," if number.rfind(",") > number.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        number = number.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif "," in number:
        # A single comma with exactly two trailing digits is a decimal comma.
        number = (
            number.replace(",", ".")
            if re.search(r",\d{1,2}$", number)
            else number.replace(",", "")
        )
    elif "." in number:
        number = (
            number
            if re.search(r"\.\d{1,2}$", number) and len(number.split(".")[0]) <= 3
            else number.replace(".", "")
        )

    try:
        parsed = float(number)
    except ValueError:
        return None
    return parsed or None


def parse_sqm(value: object) -> Optional[float]:
    """Extract a covered-area figure in square metres."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) or None

    text = str(value)
    match = _SQM_RE.search(text)
    raw = match.group(1) if match else None
    if raw is None:
        # A bare number in a field already known to be an area.
        bare = re.fullmatch(r"\s*(\d[\d.,]*)\s*", text)
        if not bare:
            return None
        raw = bare.group(1)

    raw = raw.replace(",", ".")
    if raw.count(".") > 1:  # 1.234.5 -> thousands separators
        head, _, tail = raw.rpartition(".")
        raw = head.replace(".", "") + "." + tail
    try:
        parsed = float(raw)
    except ValueError:
        return None
    return parsed or None


def parse_bedrooms(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value or None
    text = str(value)
    if re.search(r"\bstudio\b|γκαρσονιερα|γκαρσονιέρα", text, re.IGNORECASE):
        return 0
    match = _BEDROOM_RE.search(text)
    if match:
        digits = match.group(1) or match.group(2)
        if digits:
            return int(digits)
    bare = re.fullmatch(r"\s*(\d+)\s*", text)
    return int(bare.group(1)) if bare else None


def parse_year(value: object) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, int):
        return value if 1950 <= value <= _dt.date.today().year + 5 else None
    match = _YEAR_RE.search(str(value))
    return int(match.group(1)) if match else None


def classify_resale(listing: Listing) -> tuple[bool, str]:
    """Decide whether a listing is second-hand (resale) rather than new-build.

    Returns (is_resale, reason). Age wins over marketing language: an advert for
    a 2005 flat that calls itself "brand new" after a renovation is still resale.
    """
    today_year = _dt.date.today().year
    if listing.year_built:
        age = today_year - listing.year_built
        if age >= RESALE_MIN_AGE_YEARS:
            return True, f"built {listing.year_built} ({age}y old)"
        return False, f"built {listing.year_built} — too new"

    text = listing.searchable_text()
    for keyword in RESALE_KEYWORDS:
        if keyword in text:
            return True, f"advert says '{keyword}'"
    for keyword in NEW_BUILD_KEYWORDS:
        if keyword in text:
            return False, f"advert says '{keyword}'"
    # No signal either way. Resale dominates the Larnaca stock, so default to
    # keeping the listing and flag it downstream.
    return True, "no new-build signal (unverified)"


def match_area_by_text(text: str) -> Optional[str]:
    """Map a free-text location to one of the target areas."""
    if not text:
        return None
    lowered = text.lower()
    for area in AREAS.values():
        for alias in area.aliases:
            if alias in lowered:
                return area.key
    return None


def is_plausible(listing: Listing) -> bool:
    """Reject obvious parsing failures and non-residential entries."""
    t = THRESHOLDS
    if listing.price_eur is None or not (
        t.min_price_eur <= listing.price_eur <= t.max_price_eur
    ):
        return False
    if listing.area_sqm is not None and not (
        t.min_area_sqm <= listing.area_sqm <= t.max_area_sqm
    ):
        return False
    return bool(listing.url)


def dedupe(listings: Iterable[Listing]) -> list[Listing]:
    """Collapse the same flat advertised on several portals.

    Two listings are the same when the price, size and area line up. The richer
    record (more populated fields) wins, so we keep coordinates and year built
    when only one portal published them.
    """
    by_url: dict[str, Listing] = {}
    for listing in listings:
        existing = by_url.get(listing.url)
        by_url[listing.url] = _richer(existing, listing) if existing else listing

    unique: dict[tuple, Listing] = {}
    for listing in by_url.values():
        key = (
            listing.area_key or match_area_by_text(listing.location_text) or "?",
            round(listing.price_eur or 0, -3),
            round(listing.area_sqm or 0),
            listing.bedrooms,
        )
        if key[1] == 0 or key[2] == 0:
            unique[("unique", listing.url)] = listing
            continue
        existing = unique.get(key)
        unique[key] = _richer(existing, listing) if existing else listing
    return list(unique.values())


def _richer(a: Listing, b: Listing) -> Listing:
    """Pick the listing with more information, merging missing fields in."""
    def score(listing: Listing) -> int:
        fields = (
            listing.area_sqm,
            listing.bedrooms,
            listing.year_built,
            listing.lat,
            listing.description or None,
        )
        return sum(1 for value in fields if value is not None)

    winner, loser = (a, b) if score(a) >= score(b) else (b, a)
    for name in ("area_sqm", "bedrooms", "bathrooms", "floor", "year_built", "lat", "lon"):
        if getattr(winner, name) is None:
            setattr(winner, name, getattr(loser, name))
    if not winner.description:
        winner.description = loser.description
    winner.raw.setdefault("also_listed_on", []).append(loser.source)
    return winner
