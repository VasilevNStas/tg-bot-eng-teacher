"""Records LLM usage and enforces a per-user daily token budget."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, time
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.errors import AppError
from app.db.database import Database
from app.db.models import UsageLedger


class DailyBudgetExceeded(AppError):
    def __init__(self, *, spent: int, requested: int, budget: int) -> None:
        super().__init__(
            f"daily token budget exceeded: spent={spent}, requested={requested}, budget={budget}"
        )
        self.spent = spent
        self.requested = requested
        self.budget = budget


@dataclass(frozen=True, slots=True)
class UsageSnapshot:
    spent_tokens: int
    budget_tokens: int

    @property
    def remaining_tokens(self) -> int:
        return max(self.budget_tokens - self.spent_tokens, 0)

    @property
    def is_exhausted(self) -> bool:
        return self.remaining_tokens == 0


class UsageService:
    def __init__(self, database: Database, *, daily_budget: int) -> None:
        if daily_budget <= 0:
            raise ValueError("daily_budget must be positive")
        self._database = database
        self._daily_budget = daily_budget

    async def spent_today(self, user_id: int) -> int:
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
        """Build an unattached ledger row for a caller-owned transaction."""
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must not be negative")

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
    current = now or datetime.now(UTC)
    return datetime.combine(current.date(), time.min, tzinfo=UTC)
