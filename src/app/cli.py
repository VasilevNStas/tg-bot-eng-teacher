"""Command-line utilities for content import and maintenance.

Every command is safe to run from a container entrypoint: imports are
idempotent upserts by slug, and ``--dry-run`` validates without touching the
database. ``DB_PATH`` selects the SQLite file and ``CONTENT_DIR`` the bundled
content root.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from app.core.logging import setup_logging
from app.curriculum.coverage import CoverageService, format_coverage
from app.curriculum.errors import ContentError
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_path
from app.db.database import Database
from app.services.privacy.service import PrivacyService


async def import_content(path: Path, *, dry_run: bool) -> int:
    """Validate and upsert one YAML file or a directory of files.

    In dry-run mode the loader still performs full schema and cross-file
    validation, but no database connection is opened.
    """
    loaded = load_content_path(path)

    if dry_run:
        for file_path, content in loaded:
            print(
                f"{file_path}: topic={content.topic.slug} "
                f"items={len(content.items)} (validated, not imported)"
            )
        return 0

    db_path = Path(os.environ.get("DB_PATH", "./data/bot.db"))
    database = Database()
    database.init(db_path)
    try:
        importer = ContentImporter(database)
        results = await importer.import_files([content for _, content in loaded])
        for (file_path, _), result in zip(loaded, results, strict=True):
            print(
                f"{file_path}: created={result.created} "
                f"updated={result.updated} skipped={result.skipped}"
            )
    finally:
        await database.dispose()
    return 0


async def seed(*, dry_run: bool) -> int:
    """Import every bundled content file. Safe to run on each start."""
    base = Path(os.environ.get("CONTENT_DIR", "content"))
    topics = base / "topics"
    vocabulary = base / "vocabulary"

    if not topics.is_dir():
        print(f"error: content directory not found: {topics}", file=sys.stderr)
        return 1
    if not vocabulary.is_dir():
        print(f"error: content directory not found: {vocabulary}", file=sys.stderr)
        return 1

    result = await import_content(topics, dry_run=dry_run)
    if result != 0:
        return result
    return await import_content(vocabulary, dry_run=dry_run)


async def content_report(*, as_json: bool) -> int:
    """Print coverage, duplicates, and license gaps for the imported content."""
    db_path = Path(os.environ.get("DB_PATH", "./data/bot.db"))
    database = Database()
    database.init(db_path)
    try:
        report = await CoverageService(database).build()
    finally:
        await database.dispose()

    if as_json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_coverage(report))
    return 0


async def prune_history(*, days: int) -> int:
    """Delete conversations and messages older than the retention period."""
    db_path = Path(os.environ.get("DB_PATH", "./data/bot.db"))
    database = Database()
    database.init(db_path)
    try:
        deleted = await PrivacyService(database).prune_history(days)
    finally:
        await database.dispose()
    print(f"deleted conversations: {deleted}")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Parse CLI arguments and dispatch to the async command functions."""
    parser = argparse.ArgumentParser(prog="app.cli")
    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import-content",
        help="Validate and import YAML content files",
    )
    import_parser.add_argument("path", type=Path, help="YAML file or directory")
    import_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only, do not write to the database",
    )

    seed_parser = subparsers.add_parser(
        "seed",
        help="Import bundled topics and vocabulary (idempotent)",
    )
    seed_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate only, do not write to the database",
    )

    report_parser = subparsers.add_parser(
        "content-report",
        help="Show content coverage, duplicates and license gaps",
    )
    report_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )

    prune_parser = subparsers.add_parser(
        "prune-history",
        help="Delete conversations older than the retention period",
    )
    prune_parser.add_argument(
        "--days",
        type=int,
        default=None,
        help="Retention in days (default: HISTORY_RETENTION_DAYS or 90)",
    )
    args = parser.parse_args(argv)

    # Report/prune output must stay parseable, so logs go to stderr quietly.
    quiet_command = args.command in {"content-report", "prune-history"}
    setup_logging(
        "WARNING" if quiet_command else "INFO",
        stream=sys.stderr if quiet_command else sys.stdout,
    )

    try:
        if args.command == "import-content":
            return asyncio.run(import_content(args.path, dry_run=args.dry_run))
        if args.command == "seed":
            return asyncio.run(seed(dry_run=args.dry_run))
        if args.command == "content-report":
            return asyncio.run(content_report(as_json=args.json))
        if args.command == "prune-history":
            days = args.days
            if days is None:
                days = int(os.environ.get("HISTORY_RETENTION_DAYS", "90"))
            return asyncio.run(prune_history(days=days))
    except ContentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
