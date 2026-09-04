"""Logging setup: one place, called once at startup."""

from __future__ import annotations

import logging
import sys
from typing import Optional

from app.core.config import Settings, get_settings

_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"


def configure_logging(settings: Optional[Settings] = None) -> None:
    settings = settings or get_settings()
    level = getattr(logging, settings.log_level.upper(), logging.INFO)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FORMAT))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # These are chatty at INFO and say nothing we do not already log.
    for noisy in ("httpx", "httpcore", "anthropic", "openai", "urllib3"):
        logging.getLogger(noisy).setLevel(max(level, logging.WARNING))
