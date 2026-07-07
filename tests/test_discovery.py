from scraper.config import load_config
from scraper.discovery import build_search_url, has_next_page, parse_listing

LISTING_HTML = """
<html>
  <body>
    <ul>
      <li><a href="/course/detail?courseId=TGS-2024048217&serpIdKey=abc123">AI Fundamentals</a></li>
      <li><a href="/course/detail?courseId=TGS-2024099001&serpIdKey=def456">Data Analytics 101</a></li>
      <li><a href="/course/detail?courseId=TGS-2024048217&serpIdKey=abc123">AI Fundamentals (duplicate)</a></li>
    </ul>
    <nav>
      <a rel="next" href="/search?q=ai&page=2">Next</a>
    </nav>
  </body>
</html>
"""

LISTING_HTML_NO_NEXT = """
<html>
  <body>
    <ul>
      <li><a href="/course/detail?courseId=TGS-2024000111&serpIdKey=xyz">Cybersecurity Basics</a></li>
    </ul>
  </body>
</html>
"""


def test_build_search_url_encodes_keyword():
    config = load_config()
    url = build_search_url("data analytics", config)
    assert url == f"{config.base_url}/search?q=data%20analytics&termOrigin=AUTOCOMPLETE"


def test_build_search_url_includes_page_param_beyond_page_one():
    config = load_config()
    url = build_search_url("ai", config, page=3)
    assert "page=3" in url


def test_parse_listing_dedups_and_extracts_ids():
    config = load_config()
    results = parse_listing(LISTING_HTML, config.base_url)
    course_ids = [course_id for course_id, _ in results]
    assert course_ids == ["TGS-2024048217", "TGS-2024099001"]


def test_parse_listing_builds_absolute_urls():
    config = load_config()
    results = parse_listing(LISTING_HTML, config.base_url)
    for _, url in results:
        assert url.startswith(config.base_url)


def test_has_next_page_true_when_next_link_present():
    assert has_next_page(LISTING_HTML) is True


def test_has_next_page_false_when_absent():
    assert has_next_page(LISTING_HTML_NO_NEXT) is False
