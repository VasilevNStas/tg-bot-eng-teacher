"""Records LLM usage and enforces per-user daily and global monthly budgets."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.database import Database
from app.db.models import UsageLedger
from app.llm.pricing import estimate_cost_usd


class DailyBudgetExceeded(AppError):
    """Raised before an LLM call that would exceed the daily token budget."""

    def __init__(self, *, spent: int, requested: int, budget: int) -> None:
        super().__init__(
            f"daily token budget exceeded: spent={spent}, requested={requested}, budget={budget}"
        )
        self.spent = spent
        self.requested = requested
        self.budget = budget


class GlobalBudgetExceeded(AppError):
    """Raised when the project-wide monthly USD budget would be exceeded."""

    def __init__(self, *, spent_usd: Decimal, requested_usd: Decimal, budget_usd: Decimal) -> None:
        super().__init__(
            "global budget exceeded: "
            f"spent=${spent_usd}, requested=${requested_usd}, budget=${budget_usd}"
        )
        self.spent_usd = spent_usd
        self.requested_usd = requested_usd
        self.budget_usd = budget_usd


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    """Current spend versus the configured daily budget."""

    spent_tokens: int
    budget_tokens: int

    @property
    def remaining_tokens(self) -> int:
        return max(self.budget_tokens - self.spent_tokens, 0)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_tokens == 0


@dataclass(frozen=True, slots=True)
class GlobalBudgetSnapshot:
    """Current month's USD spend versus the project-wide budget."""

    spent_usd: Decimal
    budget_usd: Decimal

    @property
    def remaining_usd(self) -> Decimal:
        return max(self.budget_usd - self.spent_usd, Decimal("0"))

    @property
    def ratio(self) -> Decimal:
        if self.budget_usd <= 0:
            return Decimal("0")
        return self.spent_usd / self.budget_usd

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_usd == 0


@dataclass(frozen=True, slots=True)
class BudgetAlert:
    """A monthly budget threshold that needs to reach the admin chat."""

    threshold_percent: int
    spent_usd: Decimal
    budget_usd: Decimal


class UsageService:
    """Track LLM token spend per user per UTC day."""

    def __init__(
        self,
        database: Database,
        *,
        daily_budget: int,
        monthly_budget_usd: Decimal = Decimal("2.00"),
    ) -> None:
        if daily_budget <= 0:
            raise ValueError("daily_budget must be positive")
        if monthly_budget_usd <= 0:
            raise ValueError("monthly_budget_usd must be positive")
        self._database = database
        self._daily_budget = daily_budget
        self._monthly_budget_usd = monthly_budget_usd
        self._alert_month: tuple[int, int] | None = None
        self._alerted_thresholds: set[int] = set()

    async def spent_today(self, user_id: int) -> int:
        """Sum input+output tokens recorded since 00:00 UTC."""
        day_start = utc_day_start()
        async with self._database.session() as session:
            return await self._sum_since(session, user_id, day_start)

    async def snapshot(self, user_id: int) -> UsageSnapshot:
        return UsageSnapshot(
            spent_tokens=await self.spent_today(user_id),
            budget_tokens=self._daily_budget,
        )

    async def ensure_available(
        self,
        user_id: int,
        *,
        estimated_tokens: int = 0,
    ) -> UsageSnapshot:
        """Raise ``DailyBudgetExceeded`` if the next call would exceed budget.

        ``estimated_tokens`` is the prompt size plus the max output size, i.e.
        the worst-case cost of the call.
        """
        if estimated_tokens < 0:
            raise ValueError("estimated_tokens must not be negative")

        snapshot = await self.snapshot(user_id)
        if snapshot.spent_tokens + estimated_tokens > self._daily_budget:
            raise DailyBudgetExceeded(
                spent=snapshot.spent_tokens,
                requested=estimated_tokens,
                budget=self._daily_budget,
            )
        return snapshot

    async def spent_this_month(self, now: datetime | None = None) -> Decimal:
        """Sum provider/estimated USD cost since 00:00 UTC on the 1st."""
        month_start = utc_month_start(now)
        async with self._database.session() as session:
            total = await session.scalar(
                select(func.coalesce(func.sum(UsageLedger.cost_usd), 0)).where(
                    UsageLedger.created_at >= month_start
                )
            )
        return Decimal(str(total or 0))

    async def monthly_snapshot(self, now: datetime | None = None) -> GlobalBudgetSnapshot:
        return GlobalBudgetSnapshot(
            spent_usd=await self.spent_this_month(now),
            budget_usd=self._monthly_budget_usd,
        )

    async def ensure_global_available(
        self,
        *,
        estimated_cost_usd: Decimal,
    ) -> GlobalBudgetSnapshot:
        """Raise ``GlobalBudgetExceeded`` if the monthly USD budget is exhausted."""
        if estimated_cost_usd < 0:
            raise ValueError("estimated_cost_usd must not be negative")

        snapshot = await self.monthly_snapshot()
        if (
            snapshot.spent_usd >= self._monthly_budget_usd
            or snapshot.spent_usd + estimated_cost_usd > self._monthly_budget_usd
        ):
            raise GlobalBudgetExceeded(
                spent_usd=snapshot.spent_usd,
                requested_usd=estimated_cost_usd,
                budget_usd=self._monthly_budget_usd,
            )
        return snapshot

    async def pending_budget_alert(
        self,
        now: datetime | None = None,
    ) -> BudgetAlert | None:
        """Return the highest unalerted 80/100% threshold, if crossed."""
        snapshot = await self.monthly_snapshot(now)
        self._reset_alert_month(now)
        crossed = [
            threshold
            for threshold in (80, 100)
            if snapshot.ratio >= Decimal(threshold) / Decimal(100)
            and threshold not in self._alerted_thresholds
        ]
        if not crossed:
            return None
        return BudgetAlert(
            threshold_percent=max(crossed),
            spent_usd=snapshot.spent_usd,
            budget_usd=snapshot.budget_usd,
        )

    def mark_budget_alerted(self, alert: BudgetAlert) -> None:
        """Remember a delivered alert so the scheduler does not repeat it."""
        for threshold in (80, 100):
            if threshold <= alert.threshold_percent:
                self._alerted_thresholds.add(threshold)

    def _reset_alert_month(self, now: datetime | None = None) -> None:
        current_dt = now or datetime.now(UTC)
        current = (current_dt.year, current_dt.month)
        if self._alert_month != current:
            self._alert_month = current
            self._alerted_thresholds.clear()

    def build_entry(
        self,
        *,
        user_id: int | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        prompt_version: str | None = None,
        conversation_id: int | None = None,
        latency_ms: int | None = None,
    ) -> UsageLedger:
        """Build an unattached ledger row for a caller-owned transaction.

        Used by ``ConversationService`` so messages, corrections and usage are
        committed together.
        """
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must not be negative")
        if cost_usd < 0:
            raise ValueError("cost_usd must not be negative")
        if cost_usd == 0:
            cost_usd = estimate_cost_usd(model, input_tokens, output_tokens)

        return UsageLedger(
            user_id=user_id,
            conversation_id=conversation_id,
            model=model,
            prompt_version=prompt_version,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )

    async def record(
        self,
        *,
        user_id: int | None,
        model: str,
        input_tokens: int,
        output_tokens: int,
        cost_usd: Decimal,
        prompt_version: str | None = None,
        conversation_id: int | None = None,
        latency_ms: int | None = None,
    ) -> UsageLedger:
        """Convenience wrapper that records an entry in its own transaction."""
        entry = self.build_entry(
            user_id=user_id,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost_usd,
            prompt_version=prompt_version,
            conversation_id=conversation_id,
            latency_ms=latency_ms,
        )
        async with self._database.session() as session:
            session.add(entry)
        return entry

    @staticmethod
    async def _sum_since(
        session: AsyncSession,
        user_id: int,
        since: datetime,
    ) -> int:
        """SUM with COALESCE so a user with no calls returns 0, not NULL."""
        total = await session.scalar(
            select(
                func.coalesce(
                    func.sum(UsageLedger.input_tokens + UsageLedger.output_tokens),
                    0,
                )
            ).where(
                UsageLedger.user_id == user_id,
                UsageLedger.created_at >= since,
            )
        )
        return int(total or 0)


def utc_day_start(now: datetime | None = None) -> datetime:
    """Return midnight UTC for the given instant (defaults to now)."""
    current = now or datetime.now(UTC)
    return datetime.combine(current.date(), time.min, tzinfo=UTC)


def utc_month_start(now: datetime | None = None) -> datetime:
    """Return 00:00 UTC on the first day of the current month."""
    current = now or datetime.now(UTC)
    return datetime(current.year, current.month, 1, tzinfo=UTC)
