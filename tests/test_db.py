import sqlite3

import pytest

from scraper.discovery import CourseRef
from scraper.extraction import CourseRecord
from scraper.storage import db


@pytest.fixture
def conn(tmp_path):
    connection = db.get_connection(tmp_path / "test.db")
    db.init_db(connection)
    yield connection
    connection.close()


def _record(course_id="TGS-2024048217", **overrides) -> CourseRecord:
    defaults = dict(
        course_id=course_id,
        course_url="https://courses.myskillsfuture.gov.sg/course/1",
        course_title="AI Fundamentals",
        provider_name="Acme Training",
        training_duration="8 hours",
        training_mode="Classroom",
        fee_standard=100.00,
        fee_subsidized=30.00,
        rating_score=4.8,
        rating_count=1234,
        count_attended=5678,
        course_description="A hands-on introduction to AI fundamentals.",
        skills_gained="Machine Learning, Data Analysis",
        date_added="13 Jun 2026",
        sector_category="Information and Communications",
        language_used="English",
        search_keyword="artificial intelligence",
        scrape_status="SUCCESS",
    )
    defaults.update(overrides)
    return CourseRecord(**defaults)


def test_init_db_creates_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    }
    assert {"courses", "scrape_logs", "runs", "discovery_queue"} <= tables


def test_upsert_course_inserts_new_row(conn):
    db.upsert_course(conn, _record())
    row = conn.execute("SELECT * FROM courses WHERE course_id = ?", ("TGS-2024048217",)).fetchone()
    assert row["course_title"] == "AI Fundamentals"
    assert row["scrape_status"] == "SUCCESS"


def test_upsert_course_updates_existing_row_without_duplicating(conn):
    db.upsert_course(conn, _record(fee_standard=100.00))
    db.upsert_course(conn, _record(fee_standard=150.00))

    rows = conn.execute("SELECT * FROM courses WHERE course_id = ?", ("TGS-2024048217",)).fetchall()
    assert len(rows) == 1
    assert rows[0]["fee_standard"] == 150.00


def test_course_id_uniqueness_enforced(conn):
    db.upsert_course(conn, _record())
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO courses (course_id, course_url, search_keyword) VALUES (?, ?, ?)",
            ("TGS-2024048217", "https://example.com", "ai"),
        )


def test_enqueue_courses_dedups_within_run(conn):
    refs = [
        CourseRef("TGS-1", "https://example.com/1", "ai"),
        CourseRef("TGS-1", "https://example.com/1", "ai"),
        CourseRef("TGS-2", "https://example.com/2", "ai"),
    ]
    db.enqueue_courses(conn, "run-1", refs)
    pending = db.get_pending_queue(conn, "run-1")
    assert len(pending) == 2


def test_mark_queue_status_updates_row(conn):
    db.enqueue_courses(conn, "run-1", [CourseRef("TGS-1", "https://example.com/1", "ai")])
    db.mark_queue_status(conn, "run-1", "TGS-1", "DONE")
    pending = db.get_pending_queue(conn, "run-1")
    assert pending == []


def test_run_lifecycle(conn):
    db.create_run(conn, "run-1", ["ai", "cybersecurity"])
    assert db.get_run_keywords(conn, "run-1") == ["ai", "cybersecurity"]

    db.finish_run(conn, "run-1", "COMPLETED")
    row = conn.execute("SELECT status FROM runs WHERE run_id = ?", ("run-1",)).fetchone()
    assert row["status"] == "COMPLETED"


def test_log_failure_records_entry(conn):
    db.log_failure(
        conn,
        run_id="run-1",
        url="https://example.com/broken",
        error_type="TimeoutError",
        error_message="navigation timed out",
        retry_count=3,
    )
    row = conn.execute("SELECT * FROM scrape_logs WHERE run_id = ?", ("run-1",)).fetchone()
    assert row["error_type"] == "TimeoutError"
    assert row["retry_count"] == 3
