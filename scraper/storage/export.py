"""CSV export utility (FR-4)."""

from __future__ import annotations

import csv
import sqlite3
from pathlib import Path

COLUMNS = [
    "course_id",
    "course_url",
    "course_title",
    "provider_name",
    "training_duration",
    "training_mode",
    "fee_standard",
    "fee_subsidized",
    "rating_score",
    "rating_count",
    "count_attended",
    "course_description",
    "skills_gained",
    "date_added",
    "sector_category",
    "language_used",
    "search_keyword",
    "scrape_status",
    "scraped_at",
    "last_scraped_at",
]


def export_to_csv(
    db_path: str | Path,
    output_path: str | Path,
    *,
    keyword: str | None = None,
    since: str | None = None,
) -> int:
    """Export the `courses` table to CSV. Returns the number of rows
    written. Optional filters: `keyword` (exact search_keyword match) and
    `since` (ISO date/time string, filters on last_scraped_at)."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        query = f"SELECT {', '.join(COLUMNS)} FROM courses"  # noqa: S608 - column list is a fixed constant, not user input
        clauses = []
        params: list[str] = []
        if keyword:
            clauses.append("search_keyword = ?")
            params.append(keyword)
        if since:
            clauses.append("last_scraped_at >= ?")
            params.append(since)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY course_id"

        rows = conn.execute(query, params).fetchall()
    finally:
        conn.close()

    out_path = Path(output_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        fh.write("sep=,\n")
        writer = csv.writer(fh, dialect="excel")
        writer.writerow(COLUMNS)
        for row in rows:
            writer.writerow([row[col] for col in COLUMNS])

    return len(rows)
