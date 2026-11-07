"""Read-only product metrics: DAU, retention and completion (B-052).

The report is computed from existing raw events (user messages and exercise
sessions) instead of a separate analytics table. That keeps the schema small
and avoids emitting a second event stream; for a small deployment the scan is
cheap. If the project grows, this service is the single place to switch to a
dedicated events table (see ADR-060).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.database import Database
from app.db.enums import ExerciseSessionKind, ExerciseState, MessageRole
from app.db.models import Conversation, ExerciseSession, Message, User

DEFAULT_PERIOD_DAYS = 7
MAX_PERIOD_DAYS = 90
WEEK_DAYS = 7
MONTH_DAYS = 30


@dataclass(frozen=True, slots=True)
class DailyActive:
    """Distinct active users on one UTC day."""

    day: date
    users: int


@dataclass(frozen=True, slots=True)
class RetentionReport:
    """D1/D7 retention based on each user's first activity day.

    Only users whose first activity is old enough to observe the window are
    counted as eligible. Users who never returned are retained=0, not excluded.
    """

    d1_eligible: int
    d1_retained: int
    d7_eligible: int
    d7_retained: int

    @property
    def d1_pct(self) -> int:
        return _pct(self.d1_retained, self.d1_eligible)

    @property
    def d7_pct(self) -> int:
        return _pct(self.d7_retained, self.d7_eligible)


@dataclass(frozen=True, slots=True)
class CompletionReport:
    """Session completion for one practice kind."""

    kind: ExerciseSessionKind
    started: int
    finished: int
    abandoned: int

    @property
    def completed_total(self) -> int:
        """Sessions that reached a terminal state (active sessions excluded)."""
        return self.finished + self.abandoned

    @property
    def pct(self) -> int:
        return _pct(self.finished, self.completed_total)


@dataclass(frozen=True, slots=True)
class ProductMetrics:
    """One weekly/monthly product snapshot rendered by the CLI."""

    period_days: int
    since: datetime
    until: datetime
    total_users: int
    new_users: int
    active_users: int
    wau: int
    mau: int
    dau: tuple[DailyActive, ...]
    retention: RetentionReport
    completion: tuple[CompletionReport, ...]
    onboarding_done: int
    user_messages: int

    @property
    def onboarding_pct(self) -> int:
        return _pct(self.onboarding_done, self.total_users)

    @property
    def messages_per_active_user(self) -> float:
        if self.active_users == 0:
            return 0.0
        return round(self.user_messages / self.active_users, 1)

    def as_dict(self) -> dict[str, object]:
        """JSON-friendly representation; dates become ISO strings."""
        return {
            "period_days": self.period_days,
            "since": self.since.isoformat(),
            "until": self.until.isoformat(),
            "total_users": self.total_users,
            "new_users": self.new_users,
            "active_users": self.active_users,
            "wau": self.wau,
            "mau": self.mau,
            "dau": [{"day": item.day.isoformat(), "users": item.users} for item in self.dau],
            "retention": {
                "d1_eligible": self.retention.d1_eligible,
                "d1_retained": self.retention.d1_retained,
                "d1_pct": self.retention.d1_pct,
                "d7_eligible": self.retention.d7_eligible,
                "d7_retained": self.retention.d7_retained,
                "d7_pct": self.retention.d7_pct,
            },
            "completion": [
                {
                    "kind": item.kind.value,
                    "started": item.started,
                    "finished": item.finished,
                    "abandoned": item.abandoned,
                    "pct": item.pct,
                }
                for item in self.completion
            ],
            "onboarding_done": self.onboarding_done,
            "onboarding_pct": self.onboarding_pct,
            "user_messages": self.user_messages,
            "messages_per_active_user": self.messages_per_active_user,
        }


class ProductMetricsService:
    """Build a read-only product report from messages and exercise sessions."""

    def __init__(self, database: Database) -> None:
        self._database = database

    async def build(
        self,
        *,
        days: int = DEFAULT_PERIOD_DAYS,
        now: datetime | None = None,
    ) -> ProductMetrics:
        """Collect the report for the last ``days`` UTC days, including today."""
        if days < 1:
            raise ValueError("days must be positive")
        if days > MAX_PERIOD_DAYS:
            raise ValueError(f"days must not exceed {MAX_PERIOD_DAYS}")

        until = now or datetime.now(UTC)
        today = until.date()
        since = datetime.combine(today - timedelta(days=days - 1), time.min, tzinfo=UTC)

        async with self._database.session() as session:
            total_users = int(await session.scalar(select(func.count()).select_from(User)) or 0)
            new_users = int(
                await session.scalar(
                    select(func.count()).select_from(User).where(User.created_at >= since)
                )
                or 0
            )
            onboarding_done = int(
                await session.scalar(
                    select(func.count()).select_from(User).where(User.onboarding_state == "done")
                )
                or 0
            )

            activity = await _activity_days(session)
            user_messages = int(
                await session.scalar(
                    select(func.count())
                    .select_from(Message)
                    .join(Conversation, Conversation.id == Message.conversation_id)
                    .where(
                        Message.role == MessageRole.USER,
                        Message.created_at >= since,
                        Message.created_at <= until,
                    )
                )
                or 0
            )
            completion = await _completion(session, since=since, until=until)

        dau = tuple(
            DailyActive(
                day=day,
                users=sum(1 for active_days in activity.values() if day in active_days),
            )
            for day in _days_between(since.date(), today)
        )
        period_days = {day for day in _days_between(since.date(), today)}
        active_users = sum(1 for active_days in activity.values() if active_days & period_days)
        wau = _active_in_window(activity, today, WEEK_DAYS)
        mau = _active_in_window(activity, today, MONTH_DAYS)
        retention = _retention(activity, today)

        return ProductMetrics(
            period_days=days,
            since=since,
            until=until,
            total_users=total_users,
            new_users=new_users,
            active_users=active_users,
            wau=wau,
            mau=mau,
            dau=dau,
            retention=retention,
            completion=completion,
            onboarding_done=onboarding_done,
            user_messages=user_messages,
        )


async def _activity_days(session: AsyncSession) -> dict[int, set[date]]:
    """Map each user to the set of UTC days with a message or session start."""
    message_rows = await session.execute(
        select(Conversation.user_id, func.date(Message.created_at))
        .join(Message, Message.conversation_id == Conversation.id)
        .where(Message.role == MessageRole.USER)
        .distinct()
    )
    session_rows = await session.execute(
        select(ExerciseSession.user_id, func.date(ExerciseSession.started_at)).distinct()
    )

    activity: dict[int, set[date]] = {}
    for user_id, day in (*message_rows, *session_rows):
        activity.setdefault(int(user_id), set()).add(_as_date(day))
    return activity


async def _completion(
    session: AsyncSession,
    *,
    since: datetime,
    until: datetime,
) -> tuple[CompletionReport, ...]:
    """Count finished/abandoned sessions started in the period."""
    rows = await session.execute(
        select(ExerciseSession.kind, ExerciseSession.state, func.count())
        .where(
            ExerciseSession.started_at >= since,
            ExerciseSession.started_at <= until,
        )
        .group_by(ExerciseSession.kind, ExerciseSession.state)
    )
    counts: dict[ExerciseSessionKind, dict[ExerciseState, int]] = {}
    for kind, state, count in rows:
        counts.setdefault(kind, {})[state] = int(count)

    reports = []
    for kind in ExerciseSessionKind:
        by_state = counts.get(kind, {})
        reports.append(
            CompletionReport(
                kind=kind,
                started=sum(by_state.values()),
                finished=by_state.get(ExerciseState.FINISHED, 0),
                abandoned=by_state.get(ExerciseState.ABANDONED, 0),
            )
        )
    return tuple(reports)


def _retention(activity: Mapping[int, set[date]], today: date) -> RetentionReport:
    """Compute D1/D7 retention from first-activity cohorts."""
    d1_eligible = d1_retained = d7_eligible = d7_retained = 0
    for days in activity.values():
        first = min(days)
        if first <= today - timedelta(days=1):
            d1_eligible += 1
            if first + timedelta(days=1) in days:
                d1_retained += 1
        if first <= today - timedelta(days=7):
            d7_eligible += 1
            if any(first + timedelta(days=offset) in days for offset in range(1, 8)):
                d7_retained += 1
    return RetentionReport(
        d1_eligible=d1_eligible,
        d1_retained=d1_retained,
        d7_eligible=d7_eligible,
        d7_retained=d7_retained,
    )


def _active_in_window(activity: Mapping[int, set[date]], today: date, days: int) -> int:
    """Count users active on any of the last ``days`` days, including today."""
    window = {today - timedelta(days=offset) for offset in range(days)}
    return sum(1 for active_days in activity.values() if active_days & window)


def _days_between(start: date, end: date) -> tuple[date, ...]:
    """Inclusive list of UTC days between two dates."""
    return tuple(start + timedelta(days=offset) for offset in range((end - start).days + 1))


def _as_date(value: object) -> date:
    """Normalize SQLite ``date()`` output (str) and driver ``date`` values."""
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _pct(part: int, total: int) -> int:
    return round(part * 100 / total) if total else 0


def format_product_metrics(report: ProductMetrics) -> str:
    """Render the report as plain text for the CLI."""
    lines = [
        f"Product metrics: last {report.period_days} days "
        f"({report.since.date()} .. {report.until.date()} UTC)",
        f"Users: total={report.total_users} new={report.new_users} "
        f"active={report.active_users} WAU={report.wau} MAU={report.mau}",
        "",
        "DAU:",
    ]
    lines.extend(f"  {item.day}  {item.users}" for item in report.dau)

    lines.extend(
        [
            "",
            f"Retention: D1 {report.retention.d1_retained}/{report.retention.d1_eligible} "
            f"({report.retention.d1_pct}%), "
            f"D7 {report.retention.d7_retained}/{report.retention.d7_eligible} "
            f"({report.retention.d7_pct}%)",
            "",
            "Completion:",
        ]
    )
    for item in report.completion:
        lines.append(
            f"  {item.kind.value:<12} {item.finished}/{item.completed_total} "
            f"({item.pct}%) started={item.started}"
        )

    lines.extend(
        [
            "",
            f"Onboarding: {report.onboarding_done}/{report.total_users} ({report.onboarding_pct}%)",
            f"Messages: {report.user_messages} ({report.messages_per_active_user} per active user)",
        ]
    )
    return "\n".join(lines)
