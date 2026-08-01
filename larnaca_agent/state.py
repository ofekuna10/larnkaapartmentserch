"""Remembers which deals were already reported, so repeat runs stay quiet."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from .models import Deal


class SeenStore:
    def __init__(self, path: Path | str = "seen_listings.json"):
        self.path = Path(path)
        self._seen: dict[str, dict] = {}
        if self.path.exists():
            try:
                self._seen = json.loads(self.path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                self._seen = {}

    def is_new(self, deal: Deal) -> bool:
        """New, or re-listed at a lower price than when last reported."""
        record = self._seen.get(deal.listing.listing_id)
        if record is None:
            return True
        previous = record.get("price_eur")
        return bool(previous and deal.listing.price_eur and deal.listing.price_eur < previous)

    def filter_new(self, deals: Iterable[Deal]) -> list[Deal]:
        return [deal for deal in deals if self.is_new(deal)]

    def remember(self, deals: Iterable[Deal]) -> None:
        for deal in deals:
            self._seen[deal.listing.listing_id] = {
                "url": deal.listing.url,
                "price_eur": deal.listing.price_eur,
                "discount": round(deal.discount, 4),
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(self._seen, indent=2, ensure_ascii=False), encoding="utf-8"
        )
