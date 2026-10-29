"""Command-line utilities (content import)."""

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


async def import_content(path: Path, *, dry_run: bool) -> int:
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


async def content_report(*, as_json: bool) -> int:
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


def main(argv: Sequence[str] | None = None) -> int:
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

    report_parser = subparsers.add_parser(
        "content-report",
        help="Show content coverage, tags, duplicates and license gaps",
    )
    report_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )
    args = parser.parse_args(argv)

    report_command = args.command == "content-report"
    setup_logging(
        "WARNING" if report_command else "INFO",
        stream=sys.stderr if report_command else sys.stdout,
    )

    try:
        if args.command == "import-content":
            return asyncio.run(import_content(args.path, dry_run=args.dry_run))
        if args.command == "content-report":
            return asyncio.run(content_report(as_json=args.json))
    except ContentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
