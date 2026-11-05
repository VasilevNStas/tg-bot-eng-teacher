"""Aggregate ``usage_ledger`` for the ``llm-report`` CLI command."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select

from app.db.database import Database
from app.db.models import UsageLedger


@dataclass(frozen=True, slots=True)
class UsageRow:
    """One model's usage inside the reporting window."""

    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    avg_latency_ms: float | None


@dataclass(frozen=True, slots=True)
class UsageReport:
    """Totals plus one row per model."""

    since: datetime
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    rows: tuple[UsageRow, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "since": self.since.isoformat(),
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "cost_usd": str(self.cost_usd),
            "rows": [{**asdict(row), "cost_usd": str(row.cost_usd)} for row in self.rows],
        }


async def build_usage_report(database: Database, *, since: datetime) -> UsageReport:
    """Read usage aggregates without mutating the database."""
    async with database.session() as session:
        result = await session.execute(
            select(
                UsageLedger.model,
                func.count(UsageLedger.id),
                func.coalesce(func.sum(UsageLedger.input_tokens), 0),
                func.coalesce(func.sum(UsageLedger.output_tokens), 0),
                func.coalesce(func.sum(UsageLedger.cost_usd), 0),
                func.avg(UsageLedger.latency_ms),
            )
            .where(UsageLedger.created_at >= since)
            .group_by(UsageLedger.model)
            .order_by(UsageLedger.model)
        )
        rows = tuple(
            UsageRow(
                model=str(model),
                calls=int(calls or 0),
                input_tokens=int(input_tokens or 0),
                output_tokens=int(output_tokens or 0),
                cost_usd=Decimal(str(cost or 0)),
                avg_latency_ms=float(latency) if latency is not None else None,
            )
            for model, calls, input_tokens, output_tokens, cost, latency in result
        )

    return UsageReport(
        since=since,
        calls=sum(row.calls for row in rows),
        input_tokens=sum(row.input_tokens for row in rows),
        output_tokens=sum(row.output_tokens for row in rows),
        cost_usd=sum((row.cost_usd for row in rows), Decimal("0")),
        rows=rows,
    )


def format_usage_report(report: UsageReport) -> str:
    """Render a compact human-readable report."""
    header = (
        f"since={report.since.isoformat()} calls={report.calls} "
        f"tokens={report.input_tokens + report.output_tokens} "
        f"cost=${report.cost_usd:.6f}"
    )
    if not report.rows:
        return f"{header}\nno LLM calls"
    lines = [header, "model | calls | in | out | cost | avg_ms"]
    for row in report.rows:
        latency = f"{row.avg_latency_ms:.0f}" if row.avg_latency_ms is not None else "-"
        lines.append(
            f"{row.model} | {row.calls} | {row.input_tokens} | "
            f"{row.output_tokens} | ${row.cost_usd:.6f} | {latency}"
        )
    return "\n".join(lines)
