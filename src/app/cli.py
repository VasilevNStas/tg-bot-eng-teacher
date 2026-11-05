"""Command-line utilities for content import and maintenance.

Every command is safe to run from a container entrypoint: imports are
idempotent upserts by slug, and ``--dry-run`` validates without touching the
database. ``DB_PATH`` selects the SQLite file and ``CONTENT_DIR`` the bundled
content root.
"""

from __future__ import annotations

import argparse
import asyncio
import getpass
import json
import os
import sys
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.core.durations import parse_duration
from app.core.logging import setup_logging
from app.curriculum.coverage import CoverageService, format_coverage
from app.curriculum.errors import ContentError
from app.curriculum.importer import ContentImporter
from app.curriculum.loader import load_content_path
from app.curriculum.models import ContentFile
from app.db.database import Database
from app.services.admin.service import AdminService
from app.services.health import check_health, format_health
from app.services.privacy.service import PrivacyService
from app.services.usage.report import build_usage_report, format_usage_report


async def _import_loaded(
    loaded: list[tuple[Path, ContentFile]],
    *,
    dry_run: bool,
    prune: bool,
) -> int:
    """Import validated files and optionally retire content missing from YAML.

    ``prune`` only touches topics present in ``loaded`` and marks items
    inactive, so user progress is preserved.
    """
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
        if prune:
            deactivated = await importer.deactivate_missing([content for _, content in loaded])
            print(f"deactivated={deactivated}")
    finally:
        await database.dispose()
    return 0


async def import_content(path: Path, *, dry_run: bool, prune: bool = False) -> int:
    """Validate and upsert one YAML file or a directory of files.

    In dry-run mode the loader still performs full schema and cross-file
    validation, but no database connection is opened.
    """
    return await _import_loaded(load_content_path(path), dry_run=dry_run, prune=prune)


async def seed(*, dry_run: bool, prune: bool = False) -> int:
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

    # One batch keeps the import in a single transaction and lets --prune see
    # the complete desired set across topics and vocabulary.
    loaded = load_content_path(topics) + load_content_path(vocabulary)
    return await _import_loaded(loaded, dry_run=dry_run, prune=prune)


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


async def llm_report(*, days: int, as_json: bool) -> int:
    """Print token, cost and latency aggregates from ``usage_ledger``."""
    since = datetime.now(UTC) - timedelta(days=days)
    database = Database()
    database.init(_db_path())
    try:
        report = await build_usage_report(database, since=since)
    finally:
        await database.dispose()

    if as_json:
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    else:
        print(format_usage_report(report))
    return 0


def _db_path() -> Path:
    return Path(os.environ.get("DB_PATH", "./data/bot.db"))


def _cli_actor() -> str:
    return f"cli:{getpass.getuser()}"


async def health(*, service: str, as_json: bool) -> int:
    """Check database, migration, content, heartbeat and disk."""
    report = await check_health(_db_path(), service)
    print(report.to_json() if as_json else format_health(report))
    return 0 if report.ok else 1


async def admin_users(*, limit: int) -> int:
    """Show aggregate counters and the most recent users."""
    database = Database()
    database.init(_db_path())
    try:
        service = AdminService(database)
        counts = await service.counts()
        users = await service.list_users(limit=limit)
    finally:
        await database.dispose()

    print(
        f"users={counts.users_total} new_7d={counts.users_new_7d} "
        f"active_7d={counts.users_active_7d} "
        f"content={counts.content_items} attempts={counts.attempts_total}"
    )
    for user in users:
        state = "blocked" if user.blocked_at else ("muted" if user.muted_until else "ok")
        print(
            f"- tg_id={user.tg_id} username={user.username or '-'} "
            f"level={user.level} state={state} last_seen={user.last_seen_at or '-'}"
        )
    return 0


async def admin_user(*, tg_id: int) -> int:
    """Show one user without message contents."""
    database = Database()
    database.init(_db_path())
    try:
        info = await AdminService(database).get_user(tg_id)
    finally:
        await database.dispose()

    if info is None:
        print(f"error: user {tg_id} not found", file=sys.stderr)
        return 1
    print(f"tg_id={info.tg_id}")
    print(f"username={info.username or '-'}")
    print(f"level={info.level} timezone={info.timezone} onboarding={info.onboarding_state}")
    print(f"created_at={info.created_at.isoformat()} last_seen={info.last_seen_at or '-'}")
    print(f"blocked_at={info.blocked_at or '-'} reason={info.blocked_reason or '-'}")
    print(f"muted_until={info.muted_until or '-'} reason={info.mute_reason or '-'}")
    print(
        f"attempts={info.attempts} sessions={info.sessions} "
        f"vocabulary={info.vocabulary} bank_words_seen={info.bank_words_seen}"
    )
    return 0


async def admin_block(*, tg_id: int, reason: str) -> int:
    """Permanently block a user."""
    database = Database()
    database.init(_db_path())
    try:
        ok = await AdminService(database).block(
            tg_id,
            reason=reason,
            actor=_cli_actor(),
            source="cli",
        )
    finally:
        await database.dispose()
    if not ok:
        print(f"error: user {tg_id} not found", file=sys.stderr)
        return 1
    print(f"blocked {tg_id}")
    return 0


async def admin_unblock(*, tg_id: int, reason: str) -> int:
    """Clear a block and any mute."""
    database = Database()
    database.init(_db_path())
    try:
        ok = await AdminService(database).unblock(
            tg_id,
            reason=reason,
            actor=_cli_actor(),
            source="cli",
        )
    finally:
        await database.dispose()
    if not ok:
        print(f"error: user {tg_id} not found", file=sys.stderr)
        return 1
    print(f"unblocked {tg_id}")
    return 0


async def admin_mute(*, tg_id: int, duration: str, reason: str) -> int:
    """Temporarily restrict a user."""
    try:
        until = datetime.now(UTC) + parse_duration(duration)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    database = Database()
    database.init(_db_path())
    try:
        ok = await AdminService(database).mute(
            tg_id,
            until=until,
            reason=reason,
            actor=_cli_actor(),
            source="cli",
        )
    finally:
        await database.dispose()
    if not ok:
        print(f"error: user {tg_id} not found", file=sys.stderr)
        return 1
    print(f"muted {tg_id} until {until.isoformat()}")
    return 0


async def admin_audit(*, limit: int) -> int:
    """Print the newest administrative actions."""
    database = Database()
    database.init(_db_path())
    try:
        entries = await AdminService(database).recent_audit(limit=limit)
    finally:
        await database.dispose()

    for entry in entries:
        print(
            f"{entry.created_at.isoformat()} {entry.source} {entry.actor} "
            f"{entry.action} target={entry.target_tg_id or '-'} "
            f"ok={entry.success} details={json.dumps(entry.details, ensure_ascii=False)}"
        )
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
    seed_parser.add_argument(
        "--prune",
        action="store_true",
        help="Deactivate items that are missing from bundled topics/vocabulary",
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

    usage_parser = subparsers.add_parser(
        "llm-report",
        help="Show LLM token, cost and latency aggregates",
    )
    usage_parser.add_argument(
        "--days",
        type=int,
        default=30,
        help="Reporting window in days (default: 30)",
    )
    usage_parser.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON",
    )

    health_parser = subparsers.add_parser(
        "health",
        help="Check database, migration, content, heartbeat and disk",
    )
    health_parser.add_argument("--service", default="bot", help="Heartbeat name (bot/admin)")
    health_parser.add_argument("--json", action="store_true", help="Emit JSON")

    admin_parser = subparsers.add_parser("admin", help="Administrative lookups and containment")
    admin_sub = admin_parser.add_subparsers(dest="admin_command", required=True)

    admin_users_parser = admin_sub.add_parser("users", help="List recent users and counters")
    admin_users_parser.add_argument("--limit", type=int, default=20)

    admin_user_parser = admin_sub.add_parser("user", help="Show one user")
    admin_user_parser.add_argument("tg_id", type=int)

    block_parser = admin_sub.add_parser("block", help="Permanently block a user")
    block_parser.add_argument("tg_id", type=int)
    block_parser.add_argument("--reason", default="manual")

    unblock_parser = admin_sub.add_parser("unblock", help="Clear a block or mute")
    unblock_parser.add_argument("tg_id", type=int)
    unblock_parser.add_argument("--reason", default="")

    mute_parser = admin_sub.add_parser("mute", help="Temporarily restrict a user")
    mute_parser.add_argument("tg_id", type=int)
    mute_parser.add_argument("duration", help="30m, 24h or 7d")
    mute_parser.add_argument("--reason", default="manual")

    audit_parser = admin_sub.add_parser("audit", help="Show recent admin actions")
    audit_parser.add_argument("--limit", type=int, default=20)

    args = parser.parse_args(argv)

    # Report/prune/health output must stay parseable, so logs go to stderr.
    quiet_command = args.command in {
        "content-report",
        "prune-history",
        "llm-report",
        "health",
        "admin",
    }
    setup_logging(
        "WARNING" if quiet_command else "INFO",
        stream=sys.stderr if quiet_command else sys.stdout,
    )

    try:
        if args.command == "import-content":
            return asyncio.run(import_content(args.path, dry_run=args.dry_run))
        if args.command == "seed":
            return asyncio.run(seed(dry_run=args.dry_run, prune=args.prune))
        if args.command == "content-report":
            return asyncio.run(content_report(as_json=args.json))
        if args.command == "prune-history":
            days = args.days
            if days is None:
                days = int(os.environ.get("HISTORY_RETENTION_DAYS", "90"))
            return asyncio.run(prune_history(days=days))
        if args.command == "llm-report":
            return asyncio.run(llm_report(days=args.days, as_json=args.json))
        if args.command == "health":
            return asyncio.run(health(service=args.service, as_json=args.json))
        if args.command == "admin":
            if args.admin_command == "users":
                return asyncio.run(admin_users(limit=args.limit))
            if args.admin_command == "user":
                return asyncio.run(admin_user(tg_id=args.tg_id))
            if args.admin_command == "block":
                return asyncio.run(admin_block(tg_id=args.tg_id, reason=args.reason))
            if args.admin_command == "unblock":
                return asyncio.run(admin_unblock(tg_id=args.tg_id, reason=args.reason))
            if args.admin_command == "mute":
                return asyncio.run(
                    admin_mute(tg_id=args.tg_id, duration=args.duration, reason=args.reason)
                )
            if args.admin_command == "audit":
                return asyncio.run(admin_audit(limit=args.limit))
    except ContentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
