"""CLI entrypoint - orchestration only.

Wires together discovery (Step 1), the persisted queue, and detail
extraction (Step 2) under a configurable concurrency limit, with
resumability via --resume/--run-id. No scraping, storage, or anti-bot logic
lives in this file - it only coordinates the modules that do.
"""

from __future__ import annotations

import argparse
import asyncio
import dataclasses
import random
import sqlite3
import sys
import uuid

from scraper.browser.session import ProxyPool, SessionManager
from scraper.config import Config, load_config
from scraper.discovery import CourseRef, discover_courses
from scraper.extraction import FAILED, extract_course_detail
from scraper.logger import configure_logging, get_logger
from scraper.resilience import CircuitBreaker, RateLimiter
from scraper.storage import db

logger = get_logger("main")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="MySkillsFuture course scraper")
    parser.add_argument("--keyword", default=None, help="Single keyword to search")
    parser.add_argument("--keywords", default=None, help="Comma-separated list of keywords")
    parser.add_argument("--headful", action="store_true", help="Run with a visible browser window")
    parser.add_argument("--resume", action="store_true", help="Resume a previously interrupted run")
    parser.add_argument("--run-id", default=None, help="Run ID to resume, or to assign to a new run")
    parser.add_argument("--config", default=None, help="Path to config.yaml (default: project root)")
    return parser.parse_args(argv)


def resolve_keywords(args: argparse.Namespace) -> list[str]:
    if args.keywords:
        return [k.strip() for k in args.keywords.split(",") if k.strip()]
    if args.keyword:
        return [args.keyword.strip()]
    return []


async def _extraction_worker(
    worker_id: int,
    session_manager: SessionManager,
    queue: asyncio.Queue,
    config: Config,
    conn: sqlite3.Connection,
    run_id: str,
    rate_limiter: RateLimiter,
) -> int:
    context, page, proxy = await session_manager.new_session()
    circuit_breaker = CircuitBreaker(config)
    processed = 0
    try:
        while True:
            try:
                row = queue.get_nowait()
            except asyncio.QueueEmpty:
                break

            course_ref = CourseRef(row["course_id"], row["course_url"], row["search_keyword"])
            record = await extract_course_detail(course_ref, page, config, circuit_breaker, rate_limiter)

            db.upsert_course(conn, record)
            if record.scrape_status == FAILED:
                db.log_failure(
                    conn,
                    run_id=run_id,
                    url=course_ref.course_url,
                    error_type="EXTRACTION_FAILED",
                    error_message="Detail page extraction failed or was blocked",
                    retry_count=config.retry.max_attempts,
                )
                db.mark_queue_status(conn, run_id, course_ref.course_id, "FAILED")
            else:
                db.mark_queue_status(conn, run_id, course_ref.course_id, "DONE")
            processed += 1
    finally:
        await context.close()
    logger.info(f"Worker {worker_id} processed {processed} courses")
    return processed


async def run(config: Config, run_id: str, keywords: list[str], resuming: bool) -> None:
    conn = db.get_connection(config.db_path)
    db.init_db(conn)

    if not resuming:
        db.create_run(conn, run_id, keywords)

    proxy_pool = ProxyPool(config)
    rate_limiter = RateLimiter(config)

    async with SessionManager(config, proxy_pool) as session_manager:
        if not (resuming and db.has_queue(conn, run_id)):
            discovery_context, discovery_page, _ = await session_manager.new_session()
            discovery_breaker = CircuitBreaker(config)
            try:
                for keyword in keywords:
                    refs = await discover_courses(
                        keyword, discovery_page, config, discovery_breaker, rate_limiter
                    )
                    db.enqueue_courses(conn, run_id, refs)
                    logger.info(f"Queued {len(refs)} courses for keyword '{keyword}'")
            finally:
                await discovery_context.close()
        else:
            logger.info("Resuming run: skipping discovery, reusing persisted queue")

        pending_rows = db.get_pending_queue(conn, run_id)
        if not pending_rows:
            logger.info("No pending courses to extract")
        else:
            items = list(pending_rows)
            random.shuffle(items)  # avoid perfectly sequential/robotic traversal order
            queue: asyncio.Queue = asyncio.Queue()
            for item in items:
                queue.put_nowait(item)

            worker_count = max(1, config.concurrency.max_sessions)
            workers = [
                asyncio.create_task(
                    _extraction_worker(i, session_manager, queue, config, conn, run_id, rate_limiter)
                )
                for i in range(worker_count)
            ]
            results = await asyncio.gather(*workers)
            logger.info(f"Extraction complete: {sum(results)} courses processed")

    db.finish_run(conn, run_id, "COMPLETED")
    conn.close()


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    if args.headful:
        config = dataclasses.replace(config, headless=False)

    configure_logging(config.log_path)

    if args.resume:
        if not args.run_id:
            print("--resume requires --run-id", file=sys.stderr)
            return 2
        conn = db.get_connection(config.db_path)
        db.init_db(conn)
        keywords = db.get_run_keywords(conn, args.run_id)
        conn.close()
        if keywords is None:
            print(f"No such run_id: {args.run_id}", file=sys.stderr)
            return 2
        run_id = args.run_id
        resuming = True
    else:
        keywords = resolve_keywords(args)
        if not keywords:
            print("Provide --keyword or --keywords", file=sys.stderr)
            return 2
        run_id = args.run_id or str(uuid.uuid4())
        resuming = False

    logger.info(f"Starting run {run_id} for keywords: {keywords}")
    asyncio.run(run(config, run_id, keywords, resuming))
    logger.info(f"Run {run_id} finished")
    return 0


if __name__ == "__main__":
    sys.exit(main())
