import pytest

from scraper.config import load_config
from scraper.resilience import CircuitBreaker, detect_block


@pytest.fixture
def config():
    return load_config()


def test_detect_block_matches_known_signature(config):
    html = "<html><body>Access Denied - your request was blocked</body></html>"
    assert detect_block(html, config) == "access denied"


def test_detect_block_returns_none_for_clean_page(config):
    html = "<html><body><h1>Course Listing</h1></body></html>"
    assert detect_block(html, config) is None


def test_circuit_breaker_trips_after_threshold(config):
    breaker = CircuitBreaker(config)
    for _ in range(config.circuit_breaker.block_threshold):
        assert breaker.is_open is False
        breaker.record_block()
    assert breaker.is_open is True


def test_circuit_breaker_resets_on_success(config):
    breaker = CircuitBreaker(config)
    breaker.record_block()
    breaker.record_success()
    for _ in range(config.circuit_breaker.block_threshold - 1):
        breaker.record_block()
    assert breaker.is_open is False
