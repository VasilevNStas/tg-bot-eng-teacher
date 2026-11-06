"""Pre-flight USD estimates from the static OpenRouter price table."""

from __future__ import annotations

from decimal import Decimal

from app.llm.models import CATALOG, RouteSpec

_MILLION = Decimal("1_000_000")
_COST_QUANTUM = Decimal("0.000001")


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> Decimal:
    """Estimate a call cost from catalog prices; unknown models cost zero."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must not be negative")

    spec = CATALOG.get(model)
    if spec is None:
        return Decimal("0")

    cost = (
        Decimal(input_tokens) * spec.input_usd_per_million
        + Decimal(output_tokens) * spec.output_usd_per_million
    ) / _MILLION
    return cost.quantize(_COST_QUANTUM)


def estimate_route_cost_usd(route: RouteSpec, input_tokens: int) -> Decimal:
    """Estimate the worst case for a route, including its fallback model."""
    models = [route.model]
    if route.fallback:
        models.append(route.fallback)
    return max(
        (estimate_cost_usd(model, input_tokens, route.max_tokens) for model in models),
        default=Decimal("0"),
    )
