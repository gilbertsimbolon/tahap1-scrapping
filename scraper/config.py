"""Central configuration loader.

All tunables (delays, retries, proxy pool, UA list, concurrency, etc.) are
defined in an external YAML file (default: ``config.yaml`` at the project
root) and loaded into typed dataclasses here. Nothing in the rest of the
codebase should hardcode a timing, retry, or pool value - it must come
through this module.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config.yaml"
ENV_CONFIG_PATH_VAR = "SCRAPER_CONFIG_PATH"


@dataclass(frozen=True)
class JitterConfig:
    min_seconds: float = 2.0
    max_seconds: float = 8.0


@dataclass(frozen=True)
class RetryConfig:
    max_attempts: int = 3
    backoff_base_seconds: float = 2.0
    backoff_max_seconds: float = 30.0


@dataclass(frozen=True)
class ConcurrencyConfig:
    max_sessions: int = 2


@dataclass(frozen=True)
class PaginationConfig:
    max_pages: int = 50


@dataclass(frozen=True)
class RateLimitConfig:
    requests_per_minute: int = 12


@dataclass(frozen=True)
class CircuitBreakerConfig:
    block_threshold: int = 3
    cooldown_seconds: float = 120.0


@dataclass(frozen=True)
class WebsiteResolutionConfig:
    click_timeout_seconds: float = 15.0


@dataclass(frozen=True)
class ProxyConfig:
    enabled: bool = False
    health_check_url: str = "https://httpbin.org/ip"
    health_check_timeout_seconds: float = 8.0
    pool: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class Viewport:
    width: int
    height: int


@dataclass(frozen=True)
class Config:
    base_url: str
    db_path: str
    log_path: str
    headless: bool
    jitter: JitterConfig
    retry: RetryConfig
    concurrency: ConcurrencyConfig
    pagination: PaginationConfig
    rate_limit: RateLimitConfig
    circuit_breaker: CircuitBreakerConfig
    website_resolution: WebsiteResolutionConfig
    proxies: ProxyConfig
    user_agents: tuple[str, ...]
    viewports: tuple[Viewport, ...]
    locales: tuple[str, ...]
    timezones: tuple[str, ...]
    block_signatures: tuple[str, ...]
    config_path: Path


def _get(data: dict[str, Any], key: str, default: Any) -> Any:
    value = data.get(key)
    return default if value is None else value


def load_config(path: str | os.PathLike[str] | None = None) -> Config:
    """Load configuration from YAML, falling back to safe defaults for any
    key that is missing so a partial config file never crashes the run."""
    resolved_path = Path(path or os.environ.get(ENV_CONFIG_PATH_VAR) or DEFAULT_CONFIG_PATH)

    raw: dict[str, Any] = {}
    if resolved_path.exists():
        with resolved_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}

    jitter_raw = _get(raw, "jitter", {})
    retry_raw = _get(raw, "retry", {})
    concurrency_raw = _get(raw, "concurrency", {})
    pagination_raw = _get(raw, "pagination", {})
    rate_limit_raw = _get(raw, "rate_limit", {})
    circuit_breaker_raw = _get(raw, "circuit_breaker", {})
    website_resolution_raw = _get(raw, "website_resolution", {})
    proxies_raw = _get(raw, "proxies", {})
    viewports_raw = _get(raw, "viewports", [])

    return Config(
        base_url=_get(raw, "base_url", "https://courses.myskillsfuture.gov.sg"),
        db_path=_get(raw, "db_path", "data/courses.db"),
        log_path=_get(raw, "log_path", "data/scraper.log"),
        headless=bool(_get(raw, "headless", True)),
        jitter=JitterConfig(
            min_seconds=float(_get(jitter_raw, "min_seconds", 2.0)),
            max_seconds=float(_get(jitter_raw, "max_seconds", 8.0)),
        ),
        retry=RetryConfig(
            max_attempts=int(_get(retry_raw, "max_attempts", 3)),
            backoff_base_seconds=float(_get(retry_raw, "backoff_base_seconds", 2.0)),
            backoff_max_seconds=float(_get(retry_raw, "backoff_max_seconds", 30.0)),
        ),
        concurrency=ConcurrencyConfig(
            max_sessions=int(_get(concurrency_raw, "max_sessions", 2)),
        ),
        pagination=PaginationConfig(
            max_pages=int(_get(pagination_raw, "max_pages", 50)),
        ),
        rate_limit=RateLimitConfig(
            requests_per_minute=int(_get(rate_limit_raw, "requests_per_minute", 12)),
        ),
        circuit_breaker=CircuitBreakerConfig(
            block_threshold=int(_get(circuit_breaker_raw, "block_threshold", 3)),
            cooldown_seconds=float(_get(circuit_breaker_raw, "cooldown_seconds", 120.0)),
        ),
        website_resolution=WebsiteResolutionConfig(
            click_timeout_seconds=float(_get(website_resolution_raw, "click_timeout_seconds", 15.0)),
        ),
        proxies=ProxyConfig(
            enabled=bool(_get(proxies_raw, "enabled", False)),
            health_check_url=_get(proxies_raw, "health_check_url", "https://httpbin.org/ip"),
            health_check_timeout_seconds=float(
                _get(proxies_raw, "health_check_timeout_seconds", 8.0)
            ),
            pool=tuple(_get(proxies_raw, "pool", []) or []),
        ),
        user_agents=tuple(_get(raw, "user_agents", []) or []) or (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        ),
        viewports=tuple(Viewport(**v) for v in viewports_raw) or (Viewport(1280, 720),),
        locales=tuple(_get(raw, "locales", []) or []) or ("en-US",),
        timezones=tuple(_get(raw, "timezones", []) or []) or ("Asia/Singapore",),
        block_signatures=tuple(
            s.lower() for s in (_get(raw, "block_signatures", []) or [])
        ) or ("access denied", "captcha"),
        config_path=resolved_path,
    )
