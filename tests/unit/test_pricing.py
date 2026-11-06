from __future__ import annotations

from decimal import Decimal

import pytest

from app.llm.models import Route, RouteSpec
from app.llm.pricing import estimate_cost_usd, estimate_route_cost_usd


def test_estimate_cost_uses_input_and_output_prices() -> None:
    assert estimate_cost_usd("mistralai/mistral-nemo", 1_000_000, 0) == Decimal("0.029")
    assert estimate_cost_usd("mistralai/mistral-nemo", 0, 1_000_000) == Decimal("0.030")
    assert estimate_cost_usd("mistralai/mistral-nemo", 1000, 500) == Decimal("0.000044")


def test_unknown_model_costs_zero() -> None:
    assert estimate_cost_usd("vendor/unknown", 1_000_000, 1_000_000) == Decimal("0")


def test_negative_tokens_are_rejected() -> None:
    with pytest.raises(ValueError):
        estimate_cost_usd("mistralai/mistral-nemo", -1, 0)


def test_route_estimate_uses_most_expensive_model() -> None:
    route = RouteSpec(
        name=Route.CHAT,
        model="mistralai/mistral-nemo",
        fallback="openai/gpt-oss-20b",
        temperature=0.7,
        max_tokens=1000,
        requires_json=True,
    )

    assert estimate_route_cost_usd(route, 1_000_000) == Decimal("0.030130")


def test_route_without_fallback_uses_primary() -> None:
    route = RouteSpec(
        name=Route.CHAT,
        model="mistralai/mistral-nemo",
        fallback=None,
        temperature=0.7,
        max_tokens=1000,
        requires_json=True,
    )

    assert estimate_route_cost_usd(route, 1_000_000) == Decimal("0.029030")
