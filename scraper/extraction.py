"""Step 2: course detail-page field extraction.

Each field group has its own independently testable parser function, per
project convention - no monolithic "extract everything" function. A single
page's extraction never raises: any failure is caught, logged, and turned
into a FAILED/PARTIAL record so one bad page can never crash the run.
"""

from __future__ import annotations

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
    # Cara 1: Coba pakai label bawaan bawaan arsitektur asli
    val = _find_label_value(soup, _PROVIDER_LABELS)
    if val:
        return val
    
    # Cara 2: Cari teks apa pun yang mengandung kata 'LTD.' atau 'PTE.' atau 'CO.' 
    # (Khas penamaan nama perusahaan/vendor di Singapura)
    for el in soup.find_all(["div", "span", "p", "a"]):
        text = el.get_text(strip=True)
        text_upper = text.upper()
        if any(suffix in text_upper for suffix in ["LTD", "PTE", "CORP", "INSTITUTE", "UNIVERSITY", "SCHOOL"]) and len(text) < 60:
            # Pastikan bukan potongan teks syarat yang panjang
            if not text.startswith("http") and "eligible" not in text.lower():
                return text

    # Cara 3: Heuristic backup - ambil elemen teks non-kosong pertama di paling atas card/halaman
    h1 = soup.find("h1")
    if h1:
        # Tarik semua teks di atas judul utama, cari yang panjangnya ideal sebagai nama PT
        parent = h1.parent
        if parent:
            for sibling in h1.find_all_previous(["div", "span", "p"]):
                txt = sibling.get_text(strip=True)
                if 3 < len(txt) < 50 and "recommended" not in txt.lower():
                    return txt
                    
    return None


def extract_duration(soup: BeautifulSoup) -> str | None:
    # Cara 1: Coba pakai label bawaan dulu
    val = _find_label_value(soup, _DURATION_LABELS)
    if val:
        return val
        
    # Cara 2: Cari teks yang mengandung kata 'days' atau 'hours' (misal: 3-5 days)
    for el in soup.find_all(["span", "div", "p"]):
        text = el.get_text(strip=True).lower()
        # Jika teksnya pendek dan mengandung pola durasi khas MySkillsFuture
        if any(marker in text for marker in ["days", "days", "hours", "hrs"]) and len(text) < 15:
            # Pastikan bukan teks tombol/link panjang
            return el.get_text(strip=True)
            
    return None


def extract_mode(soup: BeautifulSoup) -> str | None:
    return _find_label_value(soup, _MODE_LABELS)


def extract_fees(soup: BeautifulSoup) -> tuple[str | None, str | None]:
    fee_standard = _find_label_value(soup, _FEE_STANDARD_LABELS)
    fee_subsidized = _find_label_value(soup, _FEE_SUBSIDIZED_LABELS)
    return fee_standard, fee_subsidized


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
            search_keyword=course_ref.search_keyword,
            scrape_status=status,
        )
    except Exception as exc:  # noqa: BLE001 - malformed DOM must not crash the run
        logger.error(
            "Unexpected error parsing detail page",
            extra={"url": course_ref.course_url, "error_type": type(exc).__name__, "retry_count": 0},
        )
        return _failed_record(course_ref)
