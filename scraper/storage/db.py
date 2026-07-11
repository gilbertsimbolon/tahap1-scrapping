"""SQLite connection, schema, and upsert logic.

This is the only module allowed to execute writes against the database -
every insert/update elsewhere in the codebase must go through one of the
functions here rather than issuing raw SQL.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Iterable

if TYPE_CHECKING:
    from scraper.discovery import CourseRef
    from scraper.extraction import CourseRecord

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS courses (
    course_id               TEXT PRIMARY KEY NOT NULL,
    course_url              TEXT NOT NULL,
    course_title            TEXT,
    provider_name           TEXT,
    training_duration       TEXT,
    training_mode           TEXT,
    fee_standard            REAL,
    fee_subsidized          REAL,
    rating_score            REAL,
    rating_count            INTEGER,
    count_attended          INTEGER,
    course_description      TEXT,
    skills_gained           TEXT,
    date_added              TEXT,
    sector_category         TEXT,
    language_used           TEXT,
    search_keyword          TEXT NOT NULL,
    scrape_status       TEXT NOT NULL DEFAULT 'SUCCESS'
                        CHECK (scrape_status IN ('SUCCESS', 'PARTIAL', 'FAILED')),
    scraped_at          TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_scraped_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS scrape_logs (
    log_id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    url             TEXT NOT NULL,
    error_type      TEXT,
    error_message   TEXT,
    retry_count     INTEGER DEFAULT 0,
    logged_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS runs (
    run_id          TEXT PRIMARY KEY NOT NULL,
    keywords        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'RUNNING'
                    CHECK (status IN ('RUNNING', 'COMPLETED', 'INTERRUPTED')),
    started_at      TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at     TIMESTAMP
);

-- Staging table for Step 1 -> Step 2 handoff. Persisting the queue (rather
-- than keeping it purely in-memory) is what makes --resume possible after a
-- crash or manual interruption (FR-2.3 / NFR-4.5).
CREATE TABLE IF NOT EXISTS discovery_queue (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id          TEXT NOT NULL,
    course_id       TEXT NOT NULL,
    course_url      TEXT NOT NULL,
    search_keyword  TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'PENDING'
                    CHECK (status IN ('PENDING', 'DONE', 'FAILED')),
    UNIQUE(run_id, course_id)
);

CREATE INDEX IF NOT EXISTS idx_search_keyword ON courses (search_keyword);
CREATE INDEX IF NOT EXISTS idx_scrape_status ON courses (scrape_status);
CREATE INDEX IF NOT EXISTS idx_run_id ON scrape_logs (run_id);
CREATE INDEX IF NOT EXISTS idx_queue_run_status ON discovery_queue (run_id, status);
"""

UPSERT_COURSE_SQL = """
INSERT INTO courses (course_id, course_url, course_title, provider_name,
                      training_duration, training_mode, fee_standard,
                      fee_subsidized, rating_score, rating_count,
                      count_attended, course_description, skills_gained,
                      date_added, sector_category,
                      language_used, search_keyword, scrape_status, last_scraped_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
ON CONFLICT(course_id) DO UPDATE SET
    course_url              = excluded.course_url,
    course_title            = excluded.course_title,
    provider_name           = excluded.provider_name,
    training_duration       = excluded.training_duration,
    training_mode           = excluded.training_mode,
    fee_standard            = excluded.fee_standard,
    fee_subsidized          = excluded.fee_subsidized,
    rating_score            = excluded.rating_score,
    rating_count            = excluded.rating_count,
    count_attended          = excluded.count_attended,
    course_description      = excluded.course_description,
    skills_gained           = excluded.skills_gained,
    date_added               = excluded.date_added,
    sector_category          = excluded.sector_category,
    language_used            = excluded.language_used,
    scrape_status            = excluded.scrape_status,
    last_scraped_at          = CURRENT_TIMESTAMP;
"""


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_SQL)
    conn.commit()


def upsert_course(conn: sqlite3.Connection, record: "CourseRecord") -> None:
    conn.execute(
        UPSERT_COURSE_SQL,
        (
            record.course_id,
            record.course_url,
            record.course_title,
            record.provider_name,
            record.training_duration,
            record.training_mode,
            record.fee_standard,
            record.fee_subsidized,
            record.rating_score,
            record.rating_count,
            record.count_attended,
            record.course_description,
            record.skills_gained,
            record.date_added,
            record.sector_category,
            record.language_used,
            record.search_keyword,
            record.scrape_status,
        ),
    )
    conn.commit()


def log_failure(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    url: str,
    error_type: str | None,
    error_message: str | None,
    retry_count: int = 0,
) -> None:
    conn.execute(
        """
        INSERT INTO scrape_logs (run_id, url, error_type, error_message, retry_count)
        VALUES (?, ?, ?, ?, ?)
        """,
        (run_id, url, error_type, error_message, retry_count),
    )
    conn.commit()


def create_run(conn: sqlite3.Connection, run_id: str, keywords: list[str]) -> None:
    conn.execute(
        "INSERT OR IGNORE INTO runs (run_id, keywords, status) VALUES (?, ?, 'RUNNING')",
        (run_id, ",".join(keywords)),
    )
    conn.commit()


def finish_run(conn: sqlite3.Connection, run_id: str, status: str) -> None:
    conn.execute(
        "UPDATE runs SET status = ?, finished_at = CURRENT_TIMESTAMP WHERE run_id = ?",
        (status, run_id),
    )
    conn.commit()


def get_run_keywords(conn: sqlite3.Connection, run_id: str) -> list[str] | None:
    row = conn.execute("SELECT keywords FROM runs WHERE run_id = ?", (run_id,)).fetchone()
    if row is None:
        return None
    return row["keywords"].split(",")


def enqueue_courses(conn: sqlite3.Connection, run_id: str, course_refs: Iterable["CourseRef"]) -> None:
    conn.executemany(
        """
        INSERT OR IGNORE INTO discovery_queue (run_id, course_id, course_url, search_keyword)
        VALUES (?, ?, ?, ?)
        """,
        [(run_id, ref.course_id, ref.course_url, ref.search_keyword) for ref in course_refs],
    )
    conn.commit()


def get_pending_queue(conn: sqlite3.Connection, run_id: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM discovery_queue WHERE run_id = ? AND status = 'PENDING'",
        (run_id,),
    ).fetchall()


def has_queue(conn: sqlite3.Connection, run_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM discovery_queue WHERE run_id = ? LIMIT 1", (run_id,)
    ).fetchone()
    return row is not None


def mark_queue_status(conn: sqlite3.Connection, run_id: str, course_id: str, status: str) -> None:
    conn.execute(
        "UPDATE discovery_queue SET status = ? WHERE run_id = ? AND course_id = ?",
        (status, run_id, course_id),
    )
    conn.commit()
