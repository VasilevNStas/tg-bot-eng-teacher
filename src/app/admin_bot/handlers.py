"""Commands for the separate admin bot.

Only containment and read-only inspection live here. Content changes,
migrations, backups and account deletion stay in the CLI/SSH control plane.
"""

from __future__ import annotations

import html
from dataclasses import dataclass
from datetime import UTC, datetime

from aiogram import Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from app.core.admin_config import AdminSettings
from app.core.durations import parse_duration
from app.services.admin.service import AdminCounts, AdminService, AdminUserInfo
from app.services.health import HealthReport, check_health, format_health


@dataclass(frozen=True, slots=True)
class AdminContext:
    """Dependencies available to every admin handler."""

    service: AdminService
    settings: AdminSettings


def _parts(message: Message) -> list[str]:
    return (message.text or "").split()[1:]


def _tg_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _actor(message: Message) -> str:
    return f"tg:{message.from_user.id}" if message.from_user is not None else "tg:unknown"


def _state(info: AdminUserInfo) -> str:
    if info.blocked_at is not None:
        return "blocked"
    if info.muted_until is not None and _as_aware(info.muted_until) > datetime.now(UTC):
        return "muted"
    return "ok"


def _as_aware(value: datetime) -> datetime:
    """SQLite may return naive datetimes; treat them as UTC."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _format_user(info: AdminUserInfo) -> str:
    lines = [
        f"tg_id: {info.tg_id}",
        f"username: {info.username or '-'}",
        f"level: {info.level}  timezone: {info.timezone}",
        f"onboarding: {info.onboarding_state}  state: {_state(info)}",
        f"created: {info.created_at.isoformat()}",
        f"last_seen: {info.last_seen_at or '-'}",
        f"blocked_at: {info.blocked_at or '-'}  reason: {info.blocked_reason or '-'}",
        f"muted_until: {info.muted_until or '-'}  reason: {info.mute_reason or '-'}",
        f"attempts: {info.attempts}  sessions: {info.sessions}",
        f"vocabulary: {info.vocabulary}  bank_words_seen: {info.bank_words_seen}",
    ]
    return f"<pre>{html.escape(chr(10).join(lines))}</pre>"


def _format_counts(counts: AdminCounts, report: HealthReport) -> str:
    state = "OK" if report.ok else "FAIL"
    return (
        "<b>Admin</b>\n"
        f"health: <b>{state}</b>\n"
        f"users: {counts.users_total} "
        f"(new 7d: {counts.users_new_7d}, active 7d: {counts.users_active_7d})\n"
        f"content: {counts.content_items} active\n"
        f"attempts: {counts.attempts_total}"
    )


async def handle_admin(message: Message, admin_context: AdminContext) -> None:
    """Dashboard: health plus aggregate counters."""
    counts = await admin_context.service.counts()
    report = await check_health(admin_context.settings.db_path, "admin")
    await message.answer(_format_counts(counts, report))


async def handle_health(message: Message, admin_context: AdminContext) -> None:
    """Detailed health checks."""
    report = await check_health(admin_context.settings.db_path, "admin")
    await message.answer(f"<pre>{html.escape(format_health(report))}</pre>")


async def handle_users(message: Message, admin_context: AdminContext) -> None:
    """Recent users with their current restriction state."""
    parts = _parts(message)
    limit = 20
    if parts:
        parsed = _tg_id(parts[0])
        if parsed is None or parsed <= 0:
            await message.answer("usage: /users [limit]")
            return
        limit = min(parsed, 100)

    counts = await admin_context.service.counts()
    users = await admin_context.service.list_users(limit=limit)
    lines = [
        f"users={counts.users_total} new_7d={counts.users_new_7d} "
        f"active_7d={counts.users_active_7d}",
    ]
    for user in users:
        lines.append(
            f"{user.tg_id} {user.username or '-'} {user.level} "
            f"{_state(user)} last_seen={user.last_seen_at or '-'}"
        )
    await message.answer(f"<pre>{html.escape(chr(10).join(lines))}</pre>")


async def handle_user(message: Message, admin_context: AdminContext) -> None:
    """Show one profile without message contents."""
    parts = _parts(message)
    if not parts or (tg_id := _tg_id(parts[0])) is None:
        await message.answer("usage: /user <tg_id>")
        return
    info = await admin_context.service.get_user(tg_id)
    if info is None:
        await message.answer("user not found")
        return
    await message.answer(_format_user(info))


async def handle_block(message: Message, admin_context: AdminContext) -> None:
    """Permanently block a user."""
    parts = _parts(message)
    if not parts or (tg_id := _tg_id(parts[0])) is None:
        await message.answer("usage: /block <tg_id> [reason]")
        return
    reason = " ".join(parts[1:]).strip() or "manual"
    ok = await admin_context.service.block(
        tg_id,
        reason=reason,
        actor=_actor(message),
        source="admin_bot",
    )
    await message.answer("blocked" if ok else "user not found")


async def handle_unblock(message: Message, admin_context: AdminContext) -> None:
    """Clear a block and any mute."""
    parts = _parts(message)
    if not parts or (tg_id := _tg_id(parts[0])) is None:
        await message.answer("usage: /unblock <tg_id> [reason]")
        return
    reason = " ".join(parts[1:]).strip()
    ok = await admin_context.service.unblock(
        tg_id,
        reason=reason,
        actor=_actor(message),
        source="admin_bot",
    )
    await message.answer("unblocked" if ok else "user not found")


async def handle_mute(message: Message, admin_context: AdminContext) -> None:
    """Temporarily restrict a user."""
    parts = _parts(message)
    if len(parts) < 2 or (tg_id := _tg_id(parts[0])) is None:
        await message.answer("usage: /mute <tg_id> <30m|24h|7d> [reason]")
        return
    try:
        until = datetime.now(UTC) + parse_duration(parts[1])
    except ValueError as exc:
        await message.answer(str(exc))
        return
    reason = " ".join(parts[2:]).strip() or "manual"
    ok = await admin_context.service.mute(
        tg_id,
        until=until,
        reason=reason,
        actor=_actor(message),
        source="admin_bot",
    )
    await message.answer(f"muted until {until.isoformat()}" if ok else "user not found")


async def handle_audit(message: Message, admin_context: AdminContext) -> None:
    """Show the newest administrative actions."""
    parts = _parts(message)
    limit = 20
    if parts:
        parsed = _tg_id(parts[0])
        if parsed is None or parsed <= 0:
            await message.answer("usage: /audit [limit]")
            return
        limit = min(parsed, 100)

    entries = await admin_context.service.recent_audit(limit=limit)
    lines = [
        f"{entry.created_at.isoformat()} {entry.source} {entry.actor} "
        f"{entry.action} target={entry.target_tg_id or '-'} ok={entry.success} "
        f"{entry.details}"
        for entry in entries
    ]
    await message.answer(f"<pre>{html.escape(chr(10).join(lines) or 'empty')}</pre>")


def build_router() -> Router:
    """Wire the admin command set."""
    router = Router(name="admin")
    router.message.register(handle_admin, CommandStart())
    router.message.register(handle_admin, Command("admin"))
    router.message.register(handle_health, Command("health"))
    router.message.register(handle_users, Command("users"))
    router.message.register(handle_user, Command("user"))
    router.message.register(handle_block, Command("block"))
    router.message.register(handle_unblock, Command("unblock"))
    router.message.register(handle_mute, Command("mute"))
    router.message.register(handle_audit, Command("audit"))
    return router
