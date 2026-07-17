from bs4 import BeautifulSoup

from scraper.discovery import CourseRef
from scraper.extraction import (
    FAILED,
    PARTIAL,
    SUCCESS,
    _classify_status,
    _failed_record,
    extract_attendance,
    extract_description,
    extract_duration,
    extract_fees,
    extract_meta_footer,
    extract_mode,
    extract_provider,
    extract_provider_contact,
    extract_rating,
    extract_skills_gained,
    extract_title,
    parse_count,
    parse_fee,
    parse_rating_score,
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

RICH_DETAIL_HTML = """
<html>
  <body>
    <h1>Certified AI Practitioner</h1>
    <div class="rating-row">
      <span>4.8</span>
      <span>4,188 ratings</span>
    </div>
    <div class="attendance-row">
      <span>11,207 have attended</span>
    </div>
    <h3>Course Description</h3>
    <p>This course equips learners with practical skills in applied artificial intelligence.</p>
    <h3>Skills you'll pick up</h3>
    <ul>
      <li>Machine Learning</li>
      <li>Data Analysis</li>
    </ul>
    <div class="meta-footer">
      <span>13 Jun 2026</span>
      <span>Information and Communications</span>
      <span>3-5 days</span>
      <span>English</span>
    </div>
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
    assert record.rating_score is None
    assert record.training_duration is None


def test_extract_rating():
    rating_score, rating_count = extract_rating(_soup(RICH_DETAIL_HTML))
    assert rating_score == "4.8"
    assert rating_count == "4,188 ratings"


def test_extract_rating_missing():
    rating_score, rating_count = extract_rating(_soup(DETAIL_HTML))
    assert rating_score is None
    assert rating_count is None


def test_extract_attendance():
    assert extract_attendance(_soup(RICH_DETAIL_HTML)) == "11,207 have attended"


def test_extract_attendance_missing():
    assert extract_attendance(_soup(DETAIL_HTML)) is None


def test_extract_description():
    description = extract_description(_soup(RICH_DETAIL_HTML))
    assert description == "This course equips learners with practical skills in applied artificial intelligence."


def test_extract_description_missing():
    assert extract_description(_soup("<html><body></body></html>")) is None


def test_extract_skills_gained():
    assert extract_skills_gained(_soup(RICH_DETAIL_HTML)) == "Machine Learning, Data Analysis"


def test_extract_skills_gained_missing():
    assert extract_skills_gained(_soup(DETAIL_HTML)) is None


def test_extract_meta_footer():
    date_added, sector_category, training_duration, language_used = extract_meta_footer(
        _soup(RICH_DETAIL_HTML)
    )
    assert date_added == "13 Jun 2026"
    assert sector_category == "Information and Communications"
    assert training_duration == "3-5 days"
    assert language_used == "English"


def test_extract_meta_footer_missing():
    date_added, sector_category, training_duration, language_used = extract_meta_footer(
        _soup(DETAIL_HTML)
    )
    assert date_added is None
    assert sector_category is None
    assert training_duration is None
    assert language_used is None


CONTACT_DETAIL_HTML = """
<html>
  <body>
    <h1>Certified AI Practitioner</h1>
    <div class="provider-contact">
      <a href="mailto:training@acme.com.sg?subject=Enquiry">Email us</a>
      <a href="tel:+6561234567">Call us</a>
      <a class="website-link" href="https://acmetraining.example.com">Visit Website</a>
      <a href="https://courses.myskillsfuture.gov.sg/terms">Terms of Use</a>
    </div>
  </body>
</html>
"""


def test_extract_provider_contact():
    email, phone, website = extract_provider_contact(_soup(CONTACT_DETAIL_HTML))
    assert email == "training@acme.com.sg"
    assert phone == "+6561234567"
    assert website == "https://acmetraining.example.com"


def test_extract_provider_contact_missing():
    email, phone, website = extract_provider_contact(_soup(DETAIL_HTML))
    assert email is None
    assert phone is None
    assert website is None


def test_extract_provider_contact_website_fallback_skips_internal_links():
    html = """
    <html><body>
      <a href="https://courses.myskillsfuture.gov.sg/terms">Terms of Use</a>
      <a href="https://partner-site.example.com">Learn More</a>
    </body></html>
    """
    _, _, website = extract_provider_contact(_soup(html))
    assert website == "https://partner-site.example.com"


def test_parse_fee():
    assert parse_fee("SGD 1200.00") == 1200.0
    assert parse_fee("SGD 1,200.50") == 1200.5


def test_parse_fee_missing():
    assert parse_fee(None) is None
    assert parse_fee("Free") is None


def test_parse_rating_score():
    assert parse_rating_score("4.8") == 4.8


def test_parse_rating_score_missing():
    assert parse_rating_score(None) is None


def test_parse_count():
    assert parse_count("1,234 ratings") == 1234
    assert parse_count("5,678 have attended") == 5678
    assert parse_count("11,207") == 11207


def test_parse_count_missing():
    assert parse_count(None) is None
    assert parse_count("no data") is None
