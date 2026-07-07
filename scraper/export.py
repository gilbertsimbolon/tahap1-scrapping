"""CLI entrypoint for exporting SQLite course data to CSV.

Orchestration only - the actual query/write logic lives in
storage/export.py. Run as: python -m scraper.export --output <path>
"""

from __future__ import annotations

import argparse
import sys

from scraper.config import load_config
from scraper.logger import configure_logging, get_logger
from scraper.storage.export import export_to_csv


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export scraped course data to CSV")
    parser.add_argument("--output", required=True, help="Path to write the CSV file to")
    parser.add_argument("--keyword", default=None, help="Filter export to a single search keyword")
    parser.add_argument("--since", default=None, help="Filter to rows last scraped on/after this ISO timestamp")
    parser.add_argument("--config", default=None, help="Path to config.yaml (default: project root)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config)
    configure_logging(config.log_path)
    logger = get_logger("export")

    row_count = export_to_csv(config.db_path, args.output, keyword=args.keyword, since=args.since)
    logger.info(f"Exported {row_count} rows to {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
