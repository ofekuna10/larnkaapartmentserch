"""HTTP layer: polite, cached page fetching with an optional browser engine.

Several Cyprus portals sit behind bot protection that rejects plain HTTP
clients. ``--engine browser`` drives a real Chromium through Playwright, which
gets past the usual JS challenge. Plain ``requests`` remains the default because
it is faster and lighter.
"""

from __future__ import annotations

import hashlib
import logging
import time
import urllib.parse
import urllib.robotparser
from pathlib import Path
from typing import Optional

from .config import (
    REQUEST_DELAY_SECONDS,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
)

log = logging.getLogger(__name__)


class FetchError(RuntimeError):
    """Raised when a page cannot be retrieved."""


class Fetcher:
    def __init__(
        self,
        engine: str = "requests",
        cache_dir: Optional[Path] = Path(".cache"),
        delay: float = REQUEST_DELAY_SECONDS,
        respect_robots: bool = True,
        timeout: int = REQUEST_TIMEOUT_SECONDS,
    ):
        self.engine = engine
        self.cache_dir = Path(cache_dir) if cache_dir else None
        self.delay = delay
        self.respect_robots = respect_robots
        self.timeout = timeout

        self._last_request_at: dict[str, float] = {}
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._session = None
        self._browser = None
        self._playwright = None

        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # ------------------------------------------------------------------ public

    def get(self, url: str, *, use_cache: bool = True) -> str:
        """Fetch a URL and return its HTML."""
        if use_cache:
            cached = self._read_cache(url)
            if cached is not None:
                log.debug("cache hit %s", url)
                return cached

        if self.respect_robots and not self.allowed(url):
            raise FetchError(f"robots.txt disallows {url}")

        self._throttle(url)
        html = (
            self._get_browser(url)
            if self.engine == "browser"
            else self._get_requests(url)
        )
        self._write_cache(url, html)
        return html

    def allowed(self, url: str) -> bool:
        """Check robots.txt for the URL's host (fail-open if unreachable)."""
        parsed = urllib.parse.urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        if host not in self._robots:
            self._robots[host] = self._load_robots(host)
        parser = self._robots[host]
        if parser is None:
            return True
        return parser.can_fetch(USER_AGENT, url)

    def _load_robots(self, host: str) -> urllib.robotparser.RobotFileParser | None:
        """Fetch and parse robots.txt. Returns None (fail-open) if unavailable."""
        import requests

        parser = urllib.robotparser.RobotFileParser()
        try:
            response = requests.get(
                f"{host}/robots.txt",
                timeout=10,
                headers={"User-Agent": USER_AGENT},
            )
        except Exception as exc:
            log.debug("robots.txt unavailable for %s (%s) — allowing", host, exc)
            return None
        if response.status_code != 200:
            return None
        parser.parse(response.text.splitlines())
        return parser

    def close(self) -> None:
        if self._browser is not None:
            try:
                self._browser.close()
            finally:
                self._browser = None
        if self._playwright is not None:
            try:
                self._playwright.stop()
            finally:
                self._playwright = None
        if self._session is not None:
            self._session.close()
            self._session = None

    def __enter__(self) -> "Fetcher":
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()

    # ----------------------------------------------------------------- engines

    def _get_requests(self, url: str) -> str:
        import requests

        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update(
                {
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-GB,en;q=0.9,el;q=0.8",
                }
            )

        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self._session.get(url, timeout=self.timeout)
                if response.status_code in (403, 429, 503):
                    raise FetchError(
                        f"HTTP {response.status_code} for {url} — the site is "
                        f"blocking automated requests; retry with --engine browser"
                    )
                response.raise_for_status()
                return response.text
            except FetchError:
                raise
            except Exception as exc:  # network hiccup — back off and retry
                last_error = exc
                time.sleep(2**attempt)
        raise FetchError(f"failed to fetch {url}: {last_error}")

    def _get_browser(self, url: str) -> str:
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as exc:
            raise FetchError(
                "--engine browser needs Playwright: "
                "pip install playwright && playwright install chromium"
            ) from exc

        if self._browser is None:
            self._playwright = sync_playwright().start()
            self._browser = self._playwright.chromium.launch(
                headless=True,
                # Headless Chromium advertises itself as automated, which is
                # what most of these portals actually reject.
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                ],
            )

        page = self._browser.new_page(
            user_agent=USER_AGENT,
            locale="en-GB",
            viewport={"width": 1440, "height": 900},
            timezone_id="Asia/Nicosia",
        )
        try:
            page.goto(url, timeout=self.timeout * 1000, wait_until="domcontentloaded")
            self._dismiss_consent(page)
            # Listings are rendered client-side and lazily; scroll to load them.
            self._autoscroll(page)
            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass  # a portal with polling XHRs never goes idle
            return page.content()
        except Exception as exc:
            raise FetchError(f"browser failed to load {url}: {exc}") from exc
        finally:
            page.close()

    @staticmethod
    def _dismiss_consent(page) -> None:
        """Click away the cookie banner that otherwise covers the results."""
        for selector in (
            "#onetrust-accept-btn-handler",
            "button#accept-cookies",
            "button[aria-label*='Accept' i]",
            "text=/^(Accept|Accept all|I agree|Συμφωνώ)$/i",
        ):
            try:
                element = page.locator(selector).first
                if element.is_visible(timeout=1200):
                    element.click(timeout=1200)
                    page.wait_for_timeout(400)
                    return
            except Exception:
                continue

    @staticmethod
    def _autoscroll(page, steps: int = 6) -> None:
        for _ in range(steps):
            try:
                page.mouse.wheel(0, 1600)
                page.wait_for_timeout(450)
            except Exception:
                return

    # ------------------------------------------------------------- plumbing

    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlparse(url).netloc
        last = self._last_request_at.get(host)
        if last is not None:
            wait = self.delay - (time.monotonic() - last)
            if wait > 0:
                time.sleep(wait)
        self._last_request_at[host] = time.monotonic()

    def _cache_path(self, url: str) -> Optional[Path]:
        if not self.cache_dir:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()
        return self.cache_dir / f"{digest}.html"

    def _read_cache(self, url: str) -> Optional[str]:
        path = self._cache_path(url)
        if path and path.exists():
            return path.read_text(encoding="utf-8")
        return None

    def _write_cache(self, url: str, html: str) -> None:
        path = self._cache_path(url)
        if path:
            path.write_text(html, encoding="utf-8")
