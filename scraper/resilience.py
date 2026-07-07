"""Retry/backoff, request pacing, block detection, and circuit breaking.

This module owns every timing and failure-handling concern around a single
page navigation: jitter delay, requests-per-minute pacing, bounded
exponential-backoff retries, anti-bot challenge detection, and the circuit
breaker that backs off a session once blocks repeat. discovery.py and
extraction.py should not implement any of this themselves - they call
:func:`goto_with_jitter` and let it decide the outcome.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque

from playwright.async_api import Page, Response

from scraper.config import Config
from scraper.logger import get_logger

logger = get_logger("resilience")


class BlockDetected(Exception):
    """Raised when a navigation lands on a known anti-bot challenge page."""

    def __init__(self, url: str, signature: str) -> None:
        super().__init__(f"Block signature '{signature}' detected at {url}")
        self.url = url
        self.signature = signature


def detect_block(html: str, config: Config) -> str | None:
    """Return the matched block signature, or None if the page looks clean."""
    lowered = html.lower()
    for signature in config.block_signatures:
        if signature in lowered:
            return signature
    return None


async def jitter_sleep(config: Config, logger_=logger) -> None:
    delay = random.uniform(config.jitter.min_seconds, config.jitter.max_seconds)
    logger_.debug("Jitter delay", extra={"error_type": None, "retry_count": 0})
    await asyncio.sleep(delay)


class RateLimiter:
    """Sliding-window limiter enforcing requests-per-minute (NFR-2.3)."""

    def __init__(self, config: Config) -> None:
        self._limit = max(1, config.rate_limit.requests_per_minute)
        self._window_seconds = 60.0
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            while self._timestamps and now - self._timestamps[0] > self._window_seconds:
                self._timestamps.popleft()
            if len(self._timestamps) >= self._limit:
                wait_for = self._window_seconds - (now - self._timestamps[0])
                if wait_for > 0:
                    logger.debug("Rate limit reached, pausing", extra={"retry_count": 0})
                    await asyncio.sleep(wait_for)
            self._timestamps.append(time.monotonic())


class CircuitBreaker:
    """Trips open after repeated consecutive block detections and forces a
    cooldown before further navigations are attempted (NFR-4.4)."""

    def __init__(self, config: Config) -> None:
        self._threshold = config.circuit_breaker.block_threshold
        self._cooldown = config.circuit_breaker.cooldown_seconds
        self._consecutive_blocks = 0
        self._open_until: float | None = None

    @property
    def is_open(self) -> bool:
        return self._open_until is not None and time.monotonic() < self._open_until

    def record_success(self) -> None:
        self._consecutive_blocks = 0

    def record_block(self) -> None:
        self._consecutive_blocks += 1
        if self._consecutive_blocks >= self._threshold:
            self._open_until = time.monotonic() + self._cooldown
            logger.warning(
                "Circuit breaker tripped after consecutive blocks; cooling down",
                extra={"retry_count": self._consecutive_blocks},
            )

    async def wait_if_open(self) -> None:
        if self.is_open:
            remaining = max(0.0, self._open_until - time.monotonic())
            logger.info("Circuit breaker open, waiting for cooldown", extra={"retry_count": 0})
            await asyncio.sleep(remaining)
            self._open_until = None
            self._consecutive_blocks = 0


async def goto_with_jitter(
    page: Page,
    url: str,
    *,
    config: Config,
    circuit_breaker: CircuitBreaker,
    rate_limiter: RateLimiter,
) -> Response | None:
    """Navigate to ``url`` applying rate limiting, circuit-breaker cooldown,
    jitter delay, and bounded exponential-backoff retries. Raises
    :class:`BlockDetected` if a challenge page is hit, or the last
    navigation exception if all retries are exhausted."""
    await circuit_breaker.wait_if_open()
    await rate_limiter.acquire()
    await jitter_sleep(config)

    last_exc: Exception | None = None
    for attempt in range(1, config.retry.max_attempts + 1):
        try:
            response = await page.goto(url, wait_until="domcontentloaded")
            html = await page.content()
            signature = detect_block(html, config)
            if signature:
                circuit_breaker.record_block()
                raise BlockDetected(url, signature)
            circuit_breaker.record_success()
            return response
        except BlockDetected:
            raise
        except Exception as exc:  # noqa: BLE001 - transient nav failure, retry bounded
            last_exc = exc
            logger.warning(
                "Navigation attempt failed, will retry" if attempt < config.retry.max_attempts else "Navigation failed, retries exhausted",
                extra={"url": url, "error_type": type(exc).__name__, "retry_count": attempt},
            )
            if attempt < config.retry.max_attempts:
                backoff = min(
                    config.retry.backoff_base_seconds * (2 ** (attempt - 1)),
                    config.retry.backoff_max_seconds,
                )
                await asyncio.sleep(backoff)

    assert last_exc is not None
    raise last_exc
