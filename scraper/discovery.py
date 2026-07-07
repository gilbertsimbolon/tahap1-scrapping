"""Step 1: search URL construction, pagination, and listing-page parsing.

Produces a de-duplicated list of (course_id, course_url) pairs for a given
keyword, ready to be queued for Step 2 detail-page extraction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import quote, urljoin

from bs4 import BeautifulSoup
from playwright.async_api import Page
import time

from scraper.config import Config
from scraper.logger import get_logger
from scraper.resilience import BlockDetected, CircuitBreaker, RateLimiter, goto_with_jitter

logger = get_logger("discovery")

COURSE_ID_PATTERN = re.compile(r"([A-Z]{2,4}-\d{6,})", re.IGNORECASE)

# NOTE: selectors below are a best-effort heuristic (course links carrying a
# `serpIdKey` param, and a "next page" control identified by rel/aria-label).
# Verify against the live DOM before a production run - MySkillsFuture may
# adjust markup without notice.
_NEXT_PAGE_SELECTOR = 'a[rel="next"], a[aria-label*="Next" i]:not([aria-disabled="true"]), button[aria-label*="Next" i]:not([disabled])'


@dataclass(frozen=True)
class CourseRef:
    course_id: str
    course_url: str
    search_keyword: str


def build_search_url(keyword: str, config: Config, page: int = 1) -> str:
    encoded = quote(keyword)
    url = f"{config.base_url}/search?q={encoded}&termOrigin=AUTOCOMPLETE"
    if page > 1:
        url += f"&page={page}"
    return url


def parse_listing(html: str, base_url: str) -> list[tuple[str, str]]:
    """Extract (course_id, course_url) pairs from a single listing page."""
    soup = BeautifulSoup(html, "html.parser")
    seen: set[str] = set()
    results: list[tuple[str, str]] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor["href"]
        if "serpIdKey" not in href and "/course/" not in href:
            continue
        match = COURSE_ID_PATTERN.search(href) or COURSE_ID_PATTERN.search(
            anchor.get_text(" ", strip=True)
        )
        if not match:
            continue
        course_id = match.group(1).upper()
        if course_id in seen:
            continue
        seen.add(course_id)
        results.append((course_id, urljoin(base_url, href)))

    return results


def has_next_page(html: str) -> bool:
    soup = BeautifulSoup(html, "html.parser")
    return soup.select_one(_NEXT_PAGE_SELECTOR) is not None


async def discover_courses(
    keyword: str,
    page: Page,
    config: Config,
    circuit_breaker: CircuitBreaker,
    rate_limiter: RateLimiter,
) -> list[CourseRef]:
    """Walk all pagination pages for one keyword and return a de-duplicated
    list of CourseRef. Stops on: no next-page control, an empty page, or the
    configured max-page safety cap (FR-1.3 / FR-1.4)."""
    all_refs: dict[str, CourseRef] = {}

    for page_number in range(1, config.pagination.max_pages + 1):
        url = build_search_url(keyword, config, page=page_number)
        try:
            await goto_with_jitter(
                page, url, config=config, circuit_breaker=circuit_breaker, rate_limiter=rate_limiter
            )
        except BlockDetected:
            logger.error(
                "Block detected during discovery; stopping pagination for keyword",
                extra={"url": url, "error_type": "BlockDetected", "retry_count": 0},
            )
            break
        except Exception as exc:  # noqa: BLE001 - never let one keyword crash the run
            logger.error(
                "Discovery navigation failed after retries; stopping pagination for keyword",
                extra={"url": url, "error_type": type(exc).__name__, "retry_count": config.retry.max_attempts},
            )
            break

        await page.wait_for_timeout(10000)

        html = await page.content()
        page_refs = parse_listing(html, config.base_url)

        print(f"--> Halaman {page_number}: Berhasil mendeteksi {len(page_refs)} link kursus.")

        if not page_refs:
            logger.info("No course listings found on page, stopping pagination", extra={"url": url})
            break

        new_count = 0
        for course_id, course_url in page_refs:
            if course_id not in all_refs:
                all_refs[course_id] = CourseRef(course_id, course_url, keyword)
                new_count += 1

        if not has_next_page(html):
            break
        if new_count == 0:
            # Pagination control present but yielded no new IDs - avoid looping forever.
            break

    logger.info(
        "Discovery complete for keyword",
        extra={"url": build_search_url(keyword, config), "retry_count": len(all_refs)},
    )
    return list(all_refs.values())
