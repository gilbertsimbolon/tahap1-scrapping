from bs4 import BeautifulSoup

from scraper.discovery import CourseRef
from scraper.extraction import (
    FAILED,
    PARTIAL,
    SUCCESS,
    _classify_status,
    _failed_record,
    extract_duration,
    extract_fees,
    extract_mode,
    extract_provider,
    extract_title,
)

DETAIL_HTML = """
<html>
  <body>
    <h1>Certified AI Practitioner</h1>
    <dl>
      <dt>Training Provider</dt><dd>Acme Training Pte Ltd</dd>
      <dt>Duration</dt><dd>24 hours</dd>
      <dt>Mode of Training</dt><dd>Classroom</dd>
      <dt>Full Course Fee</dt><dd>SGD 1200.00</dd>
      <dt>Nett Course Fee</dt><dd>SGD 360.00</dd>
    </dl>
  </body>
</html>
"""


def _soup(html: str) -> BeautifulSoup:
    return BeautifulSoup(html, "html.parser")


def test_extract_title():
    assert extract_title(_soup(DETAIL_HTML)) == "Certified AI Practitioner"


def test_extract_title_missing():
    assert extract_title(_soup("<html><body><p>No heading here</p></body></html>")) is None


def test_extract_provider():
    assert extract_provider(_soup(DETAIL_HTML)) == "Acme Training Pte Ltd"


def test_extract_duration():
    assert extract_duration(_soup(DETAIL_HTML)) == "24 hours"


def test_extract_mode():
    assert extract_mode(_soup(DETAIL_HTML)) == "Classroom"


def test_extract_fees():
    fee_standard, fee_subsidized = extract_fees(_soup(DETAIL_HTML))
    assert fee_standard == "SGD 1200.00"
    assert fee_subsidized == "SGD 360.00"


def test_extract_fees_missing():
    fee_standard, fee_subsidized = extract_fees(_soup("<html><body></body></html>"))
    assert fee_standard is None
    assert fee_subsidized is None


def test_classify_status_success():
    assert _classify_status("Title", "Provider", "8 hours", "SGD 100") == SUCCESS


def test_classify_status_partial():
    assert _classify_status("Title", None, "8 hours", "SGD 100") == PARTIAL


def test_classify_status_failed():
    assert _classify_status(None, None, None, None) == FAILED


def test_failed_record_preserves_identity_fields():
    ref = CourseRef(course_id="TGS-2024048217", course_url="https://example.com/c/1", search_keyword="ai")
    record = _failed_record(ref)
    assert record.course_id == ref.course_id
    assert record.course_url == ref.course_url
    assert record.search_keyword == ref.search_keyword
    assert record.scrape_status == FAILED
    assert record.course_title is None
