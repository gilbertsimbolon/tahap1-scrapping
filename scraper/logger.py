"""Structured logging setup.

All failure logging elsewhere in the codebase must go through the logger
returned by :func:`get_logger` and pass structured context via the ``extra``
mapping (url, error_type, retry_count) rather than string-interpolating a
free-form message. Never use ``print()`` for run output.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

_CONTEXT_FIELDS = ("url", "error_type", "retry_count", "run_id")
_CONFIGURED = False


class StructuredFormatter(logging.Formatter):
    """Emits one JSON object per line for the file handler, carrying
    whichever structured context fields (url/error_type/retry_count/run_id)
    were passed via ``extra`` on the log call."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field_name in _CONTEXT_FIELDS:
            value = getattr(record, field_name, None)
            if value is not None:
                payload[field_name] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(log_path: str | Path, level: int = logging.INFO) -> None:
    """Idempotently configure the root ``scraper`` logger. Safe to call
    multiple times (e.g. once from main.py, once from export.py)."""
    global _CONFIGURED
    if _CONFIGURED:
        return

    log_file = Path(log_path)
    log_file.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger("scraper")
    root.setLevel(level)
    root.propagate = False

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setFormatter(StructuredFormatter())
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    root.addHandler(console_handler)

    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"scraper.{name}")


def log_failure(
    logger: logging.Logger,
    *,
    url: str,
    error_type: str,
    message: str,
    retry_count: int = 0,
    run_id: str | None = None,
) -> None:
    logger.error(
        message,
        extra={"url": url, "error_type": error_type, "retry_count": retry_count, "run_id": run_id},
    )
