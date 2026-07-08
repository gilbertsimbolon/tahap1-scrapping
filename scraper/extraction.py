"""Step 2: course detail-page field extraction.

Each field group has its own independently testable parser function, per
project convention - no monolithic "extract everything" function. A single
page's extraction never raises: any failure is caught, logged, and turned
into a FAILED/PARTIAL record so one bad page can never crash the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from bs4 import BeautifulSoup
from playwright.async_api import Page

from scraper.config import Config
from scraper.discovery import CourseRef
from scraper.logger import get_logger
from scraper.resilience import CircuitBreaker, RateLimiter, goto_with_jitter

logger = get_logger("extraction")

SUCCESS = "SUCCESS"
PARTIAL = "PARTIAL"
FAILED = "FAILED"

# NOTE: label lists below are a best-effort heuristic against typical
# MySkillsFuture detail-page markup (label/value pairs). Verify against the
# live DOM before a production run and adjust as the site's markup changes.
_PROVIDER_LABELS = ["Training Provider", "Provider"]
_DURATION_LABELS = ["Duration", "Training Duration", "Total Training Duration"]
_MODE_LABELS = ["Mode of Training", "Training Mode", "Mode", "Classroom"]
_FEE_STANDARD_LABELS = ["Full Course Fee", "Course Fee (Before GST)", "Course Fee", "Full course fee:"]
_FEE_SUBSIDIZED_LABELS = ["SkillsFuture Subsidies", "Nett Course Fee", "Nett Fee", "Fee After Subsidy"]

# The fields below (rating, attendance, description, skills, meta footer)
# render as icon/visual elements on the live page rather than label/value
# pairs, so they can't go through `_find_label_value` - each has its own
# heuristic, selector-based parser instead.
_RATING_SCORE_PATTERN = re.compile(r"^\d(?:\.\d)?$")
_RATING_COUNT_PATTERN = re.compile(r"^[\d,]+\s+ratings?$", re.IGNORECASE)
_DATE_ADDED_PATTERN = re.compile(
    r"\b\d{1,2}\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s+\d{4}\b",
    re.IGNORECASE,
)
_DURATION_PATTERN = re.compile(
    r"\b(\d+(?:\s*[-–—]\s*\d+)?\s*(?:hours?|hrs?|days?))\b", re.IGNORECASE
)
_DESCRIPTION_HEADING_MARKERS = ["course description", "about this course", "description"]
_SKILLS_HEADING_MARKERS = ["skills you'll pick up", "skills you will pick up", "what you'll learn"]
_MAX_META_TOKEN_LEN = 60

# Finite reference lists used to recognise unlabeled meta-footer tokens
# (sector / language) by value rather than by a nearby label.
_KNOWN_SECTORS = [
    "Information and Communications",
    "Financial Services",
    "Healthcare",
    "Retail",
    "Manufacturing",
    "Built Environment",
    "Education",
    "Food Services",
    "Hotel and Accommodation Services",
    "Logistics",
    "Security",
    "Tourism",
    "Wholesale Trade",
    "Human Resource",
    "Public Service",
    "Early Childhood Care and Education",
    "Energy and Chemicals",
    "Marine and Offshore",
    "Precision Engineering",
]
_KNOWN_LANGUAGES = ["English", "Mandarin", "Malay", "Tamil", "Chinese", "Bahasa Indonesia", "Bilingual"]


@dataclass(frozen=True)
class CourseRecord:
    course_id: str
    course_url: str
    course_title: str | None
    provider_name: str | None
    training_duration: str | None
    training_mode: str | None
    fee_standard: str | None
    fee_subsidized: str | None
    rating_score: str | None
    rating_count: str | None
    count_attended: str | None
    course_description: str | None
    skills_gained: str | None
    date_added: str | None
    sector_category: str | None
    language_used: str | None
    search_keyword: str
    scrape_status: str


def _find_label_value(soup: BeautifulSoup, labels: list[str]) -> str | None:
    normalized_labels = {label.lower().rstrip(":") for label in labels}
    for el in soup.find_all(["dt", "th", "span", "div", "label", "p", "strong"]):
        text = el.get_text(strip=True).lower().rstrip(":")
        if text not in normalized_labels:
            continue
        sibling = el.find_next_sibling(["dd", "td", "span", "div", "p"])
        if sibling:
            value = sibling.get_text(strip=True)
            if value:
                return value
        parent = el.parent
        if parent:
            full_text = parent.get_text(" ", strip=True)
            for label in labels:
                if full_text.lower().startswith(label.lower()):
                    remainder = full_text[len(label):].lstrip(": ").strip()
                    if remainder:
                        return remainder
    return None


def extract_title(soup: BeautifulSoup) -> str | None:
    h1 = soup.find("h1")
    if h1:
        text = h1.get_text(strip=True)
        return text or None
    return None


def extract_provider(soup: BeautifulSoup) -> str | None:
    # First, try the standard label-based lookup.
    val = _find_label_value(soup, _PROVIDER_LABELS)
    if val:
        return val

    # Otherwise, look for any text containing 'LTD', 'PTE', or 'CO' - typical
    # suffixes for Singapore company/vendor names.
    for el in soup.find_all(["div", "span", "p", "a"]):
        text = el.get_text(strip=True)
        text_upper = text.upper()
        if any(suffix in text_upper for suffix in ["LTD", "PTE", "CORP", "INSTITUTE", "UNIVERSITY", "SCHOOL"]) and len(text) < 60:
            # Make sure this isn't a snippet from a long eligibility/terms block.
            if not text.startswith("http") and "eligible" not in text.lower():
                return text

    # Last resort heuristic: take the first non-empty text element near the
    # top of the page.
    h1 = soup.find("h1")
    if h1:
        # Scan all text above the main heading, looking for a plausible
        # provider-name length.
        parent = h1.parent
        if parent:
            for sibling in h1.find_all_previous(["div", "span", "p"]):
                txt = sibling.get_text(strip=True)
                if 3 < len(txt) < 50 and "recommended" not in txt.lower():
                    return txt

    return None


def extract_duration(soup: BeautifulSoup) -> str | None:
    # First, try the standard label-based lookup.
    val = _find_label_value(soup, _DURATION_LABELS)
    if val:
        return val

    # Otherwise, take the hour-based value, since day-based values are
    # already handled by meta_footer.
    for el in soup.find_all(["span", "div", "p"]):
        text = el.get_text(strip=True).lower()
        if any(marker in text for marker in ["days", "day"]) and len(text) < 15:
            return el.get_text(strip=True)
    return None


def extract_duration_fallback(soup: BeautifulSoup) -> str | None:
    """Aggressive fallback for `training_duration`.

    The tag-based loop in `extract_meta_footer` misses values when the live
    markup splits the number and the unit ("hours"/"hrs"/"day"/"days")
    across whitespace/newlines inside a single text node. This scans every
    raw text node directly (`soup.find_all(string=True)`), bypassing tag
    hierarchy, and normalises whitespace before matching.
    """
    for text_node in soup.find_all(string=True):
        clean_text = " ".join(text_node.split()).strip()
        if not clean_text:
            continue
        match = _DURATION_PATTERN.search(clean_text)
        if match:
            return match.group(1).strip()
    return None


def extract_mode(soup: BeautifulSoup) -> str | None:
    return _find_label_value(soup, _MODE_LABELS)


def extract_fees(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    fee_standard = _find_label_value(soup, _FEE_STANDARD_LABELS)
    fee_subsidized = _find_label_value(soup, _FEE_SUBSIDIZED_LABELS)
    return fee_standard, fee_subsidized


def extract_rating(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    """Rating score/count render as bare numbers next to a star icon, with
    no text label - matched by value shape instead."""
    rating_score = None
    rating_count = None
    for el in soup.find_all(["span", "div", "p"]):
        text = el.get_text(strip=True)
        if not text or len(text) > _MAX_META_TOKEN_LEN:
            continue
        if rating_score is None and _RATING_SCORE_PATTERN.match(text):
            rating_score = text
        elif rating_count is None and _RATING_COUNT_PATTERN.match(text):
            rating_count = text
        if rating_score and rating_count:
            break
    return rating_score, rating_count


def extract_attendance(soup: BeautifulSoup) -> str | None:
    """Finds elements containing 'have attended' and captures only the
    number immediately preceding that phrase."""

    for el in soup.find_all(["span", "div", "p"]):
        text = el.get_text(" ", strip=True)
        if text and "have attended" in text.lower():
            # Capture the number/comma sequence right before 'have attended'.
            match = re.search(r'([\d,]+)\s+have attended', text, re.IGNORECASE)
            if match:
                return match.group(1)

    # Fallback: scan raw text nodes directly, in case the value is split
    # across separate tags.
    for node in soup.find_all(string=re.compile(r"have attended", re.IGNORECASE)):
        text = node.strip()
        # Try matching against the text node itself, or the combined text of its parent.
        parent_text = node.parent.get_text(" ", strip=True) if node.parent else text
        match = re.search(r'([\d,]+)\s+have attended', parent_text, re.IGNORECASE)
        if match:
            return match.group(1)

    return None


def extract_description(soup: BeautifulSoup) -> str | None:
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b"]):
        heading_text = heading.get_text(strip=True).lower()
        if any(marker in heading_text for marker in _DESCRIPTION_HEADING_MARKERS):
            sibling = heading.find_next(["p", "div"])
            if sibling:
                text = sibling.get_text(" ", strip=True)
                if text:
                    return text

    # Fallback: no recognisable heading - take the longest paragraph on the
    # page, since the description is typically the largest block of prose.
    paragraphs = [p.get_text(" ", strip=True) for p in soup.find_all("p")]
    paragraphs = [p for p in paragraphs if len(p) > 40]
    if paragraphs:
        return max(paragraphs, key=len)
    return None


def extract_skills_gained(soup: BeautifulSoup) -> str | None:
    for heading in soup.find_all(["h2", "h3", "h4", "strong", "b", "span"]):
        heading_text = heading.get_text(strip=True).lower()
        if any(marker in heading_text for marker in _SKILLS_HEADING_MARKERS):
            container = heading.find_next(["ul", "ol", "div", "p"])
            if not container:
                continue
            items = [li.get_text(strip=True) for li in container.find_all("li")]
            items = [item for item in items if item]
            if items:
                return ", ".join(items)
            text = container.get_text(" ", strip=True)
            if text:
                return text
    return None


def extract_meta_footer(soup: BeautifulSoup) -> tuple[str | None, str | None, str | None, str | None]:
    """Parses the icon-driven meta footer line (date added / sector /
    duration / language). Each token has no static label, so it's matched
    either against a date/duration pattern or a finite reference list of
    known sector and language values.

    The duration token here is a supplementary source for `training_duration`
    - it's combined with `extract_duration`'s label-based lookup by the
    caller, since the meta footer only carries a value when the live markup
    renders it as an icon/token rather than a label/value pair.
    """
    date_added = None
    sector_category = None
    training_duration = None
    language_used = None

    for el in soup.find_all(["span", "div", "li", "p"]):
        text = el.get_text(strip=True)
        if not text or len(text) > _MAX_META_TOKEN_LEN:
            continue

        if date_added is None:
            match = _DATE_ADDED_PATTERN.search(text)
            if match:
                date_added = match.group(0)

        if (
            training_duration is None
            and any(marker in text.lower() for marker in ("hours", "hrs", "day", "days"))
            and any(ch.isdigit() for ch in text)
            and len(text) < 15
        ):
            training_duration = text

        if sector_category is None and text in _KNOWN_SECTORS:
            sector_category = text

        if language_used is None and text in _KNOWN_LANGUAGES:
            language_used = text

        if date_added and training_duration and sector_category and language_used:
            break

    if training_duration is None:
        training_duration = extract_duration_fallback(soup)

    return date_added, sector_category, training_duration, language_used


def _classify_status(
    course_title: str | None,
    provider_name: str | None,
    training_duration: str | None,
    fee_standard: str | None,
) -> str:
    mandatory = (course_title, provider_name, training_duration, fee_standard)
    if all(mandatory):
        return SUCCESS
    if any(mandatory):
        return PARTIAL
    return FAILED


def _failed_record(course_ref: CourseRef) -> CourseRecord:
    return CourseRecord(
        course_id=course_ref.course_id,
        course_url=course_ref.course_url,
        course_title=None,
        provider_name=None,
        training_duration=None,
        training_mode=None,
        fee_standard=None,
        fee_subsidized=None,
        rating_score=None,
        rating_count=None,
        count_attended=None,
        course_description=None,
        skills_gained=None,
        date_added=None,
        sector_category=None,
        language_used=None,
        search_keyword=course_ref.search_keyword,
        scrape_status=FAILED,
    )


async def extract_course_detail(
    course_ref: CourseRef,
    page: Page,
    config: Config,
    circuit_breaker: CircuitBreaker,
    rate_limiter: RateLimiter,
) -> CourseRecord:
    """Navigate to and extract one course detail page. Always returns a
    CourseRecord (never raises) - the caller is responsible for persisting
    it and logging the underlying error to the audit table."""
    try:
        await goto_with_jitter(
            page,
            course_ref.course_url,
            config=config,
            circuit_breaker=circuit_breaker,
            rate_limiter=rate_limiter,
        )
    except Exception as exc:  # noqa: BLE001 - one page must never crash the run
        logger.error(
            "Detail page navigation failed after retries",
            extra={"url": course_ref.course_url, "error_type": type(exc).__name__, "retry_count": config.retry.max_attempts},
        )
        return _failed_record(course_ref)

    try:
        html = await page.content()
        soup = BeautifulSoup(html, "html.parser")

        title = extract_title(soup)
        provider = extract_provider(soup)
        duration = extract_duration(soup)
        mode = extract_mode(soup)
        fee_standard, fee_subsidized = extract_fees(soup)
        rating_score, rating_count = extract_rating(soup)
        count_attended = extract_attendance(soup)
        course_description = extract_description(soup)
        skills_gained = extract_skills_gained(soup)
        date_added, sector_category, duration_from_meta, language_used = extract_meta_footer(soup)
        if not duration:
            duration = duration_from_meta

        status = _classify_status(title, provider, duration, fee_standard)
        if status != SUCCESS:
            logger.warning(
                "Detail page extraction incomplete",
                extra={"url": course_ref.course_url, "error_type": status, "retry_count": 0},
            )

        return CourseRecord(
            course_id=course_ref.course_id,
            course_url=course_ref.course_url,
            course_title=title,
            provider_name=provider,
            training_duration=duration,
            training_mode=mode,
            fee_standard=fee_standard,
            fee_subsidized=fee_subsidized,
            rating_score=rating_score,
            rating_count=rating_count,
            count_attended=count_attended,
            course_description=course_description,
            skills_gained=skills_gained,
            date_added=date_added,
            sector_category=sector_category,
            language_used=language_used,
            search_keyword=course_ref.search_keyword,
            scrape_status=status,
        )
    except Exception as exc:  # noqa: BLE001 - malformed DOM must not crash the run
        logger.error(
            "Unexpected error parsing detail page",
            extra={"url": course_ref.course_url, "error_type": type(exc).__name__, "retry_count": 0},
        )
        return _failed_record(course_ref)
