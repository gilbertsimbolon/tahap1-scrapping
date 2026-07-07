"""Browser/context lifecycle and proxy assignment.

Scraping code must never instantiate a raw Playwright context directly -
it must go through :class:`SessionManager`, which guarantees every context
is stealth-masked, fingerprint-randomized, and (when proxies are enabled)
routed through the rotation pool.
"""

from __future__ import annotations

import asyncio
import random
import urllib.error
import urllib.request
from dataclasses import dataclass

from playwright.async_api import Browser, BrowserContext, Page, async_playwright

from scraper.browser.stealth import apply_stealth
from scraper.config import Config
from scraper.logger import get_logger

logger = get_logger("browser.session")


@dataclass
class Fingerprint:
    user_agent: str
    viewport: dict[str, int]
    locale: str
    timezone_id: str


def random_fingerprint(config: Config) -> Fingerprint:
    viewport = random.choice(config.viewports)
    return Fingerprint(
        user_agent=random.choice(config.user_agents),
        viewport={"width": viewport.width, "height": viewport.height},
        locale=random.choice(config.locales),
        timezone_id=random.choice(config.timezones),
    )


class ProxyPool:
    """Rotating pool of proxy endpoints with health checks and dead-proxy
    exclusion (NFR-3.1 / NFR-3.2 / NFR-3.4)."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._pool: list[str] = list(config.proxies.pool)
        self._dead: set[str] = set()

    @property
    def enabled(self) -> bool:
        return self._config.proxies.enabled and bool(self._pool)

    def _candidates(self) -> list[str]:
        return [p for p in self._pool if p not in self._dead]

    def health_check(self, proxy: str) -> bool:
        """Synchronous health check; callers should run this via
        ``asyncio.to_thread`` to avoid blocking the event loop."""
        try:
            handler = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
            opener = urllib.request.build_opener(handler)
            with opener.open(
                self._config.proxies.health_check_url,
                timeout=self._config.proxies.health_check_timeout_seconds,
            ) as resp:
                return 200 <= resp.status < 400
        except (urllib.error.URLError, TimeoutError, OSError):
            return False

    def mark_dead(self, proxy: str) -> None:
        logger.warning("Marking proxy dead for remainder of run", extra={"url": proxy})
        self._dead.add(proxy)

    def get_proxy(self) -> str | None:
        """Return a healthy proxy, or ``None`` if proxies are disabled or
        the pool is exhausted (scraping continues direct in that case, but
        every caller still goes through this method rather than hardcoding
        a connection)."""
        if not self.enabled:
            return None
        candidates = self._candidates()
        while candidates:
            proxy = random.choice(candidates)
            if self.health_check(proxy):
                return proxy
            self.mark_dead(proxy)
            candidates = self._candidates()
        logger.warning("Proxy pool exhausted; falling back to direct connection")
        return None


class SessionManager:
    """Owns a single Playwright browser instance for the lifetime of a run.
    Each call to :meth:`new_session` creates one stealth-masked context with
    its own randomized fingerprint and (optionally) its own proxy - cookies
    persist for the lifetime of that context so a batch of navigations looks
    like one continuous human session rather than repeated fresh visits."""

    def __init__(self, config: Config, proxy_pool: ProxyPool | None = None) -> None:
        self._config = config
        self._proxy_pool = proxy_pool or ProxyPool(config)
        self._playwright = None
        self._browser: Browser | None = None

    async def __aenter__(self) -> "SessionManager":
        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(headless=self._config.headless)
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def new_session(self) -> tuple[BrowserContext, Page, str | None]:
        """Create a new stealth context + page with a fresh randomized
        fingerprint and proxy assignment. Returns (context, page, proxy)."""
        assert self._browser is not None, "SessionManager must be used as an async context manager"

        fingerprint = random_fingerprint(self._config)
        # get_proxy() performs a blocking network health check; keep it off
        # the event loop.
        proxy = await asyncio.to_thread(self._proxy_pool.get_proxy)

        context_kwargs: dict = {
            "user_agent": fingerprint.user_agent,
            "viewport": fingerprint.viewport,
            "locale": fingerprint.locale,
            "timezone_id": fingerprint.timezone_id,
        }
        if proxy:
            context_kwargs["proxy"] = {"server": proxy}

        context = await self._browser.new_context(**context_kwargs)
        await apply_stealth(context)
        page = await context.new_page()
        return context, page, proxy

    def report_proxy_failure(self, proxy: str | None) -> None:
        if proxy:
            self._proxy_pool.mark_dead(proxy)
