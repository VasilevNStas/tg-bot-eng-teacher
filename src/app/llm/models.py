"""Task routes and the known OpenRouter model catalog.

Routing is intentionally separate from the transport adapter: services ask for
a route (chat, correction, summary, judge), and the route decides which model,
temperature and token cap to use. The catalog carries only metadata we use for
validation and reporting; provider usage remains the source of truth for cost.

Prices are a snapshot of the public OpenRouter catalog (2026-11-05) and can
change. Unknown models are allowed but reported as warnings at startup, so a
typo is visible without making the catalog a hard gate.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from app.core.config import Settings


class Route(StrEnum):
    """A logical LLM task with its own model and generation settings."""

    CHAT = "chat"
    CORRECTION = "correction"
    ROLEPLAY = "roleplay"
    SUMMARY = "summary"
    JUDGE = "judge"


@dataclass(frozen=True, slots=True)
class ModelSpec:
    """Static metadata for one provider model."""

    id: str
    input_usd_per_million: Decimal
    output_usd_per_million: Decimal
    context_window: int
    json_schema: bool = True


# Keep this list focused on the cheap candidates from ADR-052. The catalog is
# metadata, not an allowlist: unknown IDs are allowed with a startup warning.
CATALOG: dict[str, ModelSpec] = {
    "mistralai/mistral-nemo": ModelSpec(
        id="mistralai/mistral-nemo",
        input_usd_per_million=Decimal("0.029"),
        output_usd_per_million=Decimal("0.030"),
        context_window=131_072,
    ),
    "openai/gpt-oss-20b": ModelSpec(
        id="openai/gpt-oss-20b",
        input_usd_per_million=Decimal("0.030"),
        output_usd_per_million=Decimal("0.130"),
        context_window=131_072,
    ),
    "qwen/qwen3.7-flash": ModelSpec(
        id="qwen/qwen3.7-flash",
        input_usd_per_million=Decimal("0.030"),
        output_usd_per_million=Decimal("0.130"),
        context_window=1_048_576,
    ),
    "google/gemini-2.5-flash-lite": ModelSpec(
        id="google/gemini-2.5-flash-lite",
        input_usd_per_million=Decimal("0.100"),
        output_usd_per_million=Decimal("0.400"),
        context_window=1_048_576,
    ),
    "mistralai/mistral-small-24b-instruct-2501": ModelSpec(
        id="mistralai/mistral-small-24b-instruct-2501",
        input_usd_per_million=Decimal("0.050"),
        output_usd_per_million=Decimal("0.080"),
        context_window=32_768,
    ),
    "qwen/qwen3-32b": ModelSpec(
        id="qwen/qwen3-32b",
        input_usd_per_million=Decimal("0.080"),
        output_usd_per_million=Decimal("0.280"),
        context_window=131_072,
    ),
    "openai/gpt-4o-mini": ModelSpec(
        id="openai/gpt-4o-mini",
        input_usd_per_million=Decimal("0.150"),
        output_usd_per_million=Decimal("0.600"),
        context_window=128_000,
    ),
    "anthropic/claude-haiku-5.5": ModelSpec(
        id="anthropic/claude-haiku-5.5",
        input_usd_per_million=Decimal("0.100"),
        output_usd_per_million=Decimal("0.500"),
        context_window=1_000_000,
    ),
    "deepseek/deepseek-v3.2": ModelSpec(
        id="deepseek/deepseek-v3.2",
        input_usd_per_million=Decimal("0.260"),
        output_usd_per_million=Decimal("0.380"),
        context_window=163_840,
    ),
}


@dataclass(frozen=True, slots=True)
class RouteSpec:
    """Concrete generation settings for one logical route."""

    name: Route
    model: str
    fallback: str | None
    temperature: float
    max_tokens: int
    requires_json: bool


@dataclass(frozen=True, slots=True)
class RouteValidation:
    """Startup validation result: hard errors and non-fatal warnings."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.errors


def build_routes(settings: Settings) -> dict[Route, RouteSpec]:
    """Resolve route settings from environment, clamping output tokens."""
    fast = settings.llm_model_fast.strip()
    smart = settings.llm_model_smart.strip()
    fallback = (settings.llm_model_fallback or "").strip() or None
    cap = settings.llm_max_output_tokens

    def spec(
        name: Route,
        *,
        model: str,
        temperature: float,
        max_tokens: int,
        requires_json: bool,
    ) -> RouteSpec:
        model = model.strip()
        # A single global fallback may coincide with one route's primary (for
        # example smart -> fast). That route simply has no fallback.
        route_fallback = fallback if fallback and fallback != model else None
        return RouteSpec(
            name=name,
            model=model,
            fallback=route_fallback,
            temperature=temperature,
            max_tokens=min(max_tokens, cap),
            requires_json=requires_json,
        )

    return {
        Route.CHAT: spec(
            Route.CHAT,
            model=fast,
            temperature=settings.llm_temperature_chat,
            max_tokens=settings.llm_max_tokens_chat,
            requires_json=True,
        ),
        Route.CORRECTION: spec(
            Route.CORRECTION,
            model=smart,
            temperature=settings.llm_temperature_correction,
            max_tokens=settings.llm_max_tokens_correction,
            requires_json=True,
        ),
        Route.ROLEPLAY: spec(
            Route.ROLEPLAY,
            model=settings.llm_model_roleplay or fast,
            temperature=settings.llm_temperature_roleplay,
            max_tokens=settings.llm_max_tokens_roleplay,
            requires_json=False,
        ),
        Route.SUMMARY: spec(
            Route.SUMMARY,
            model=settings.llm_model_summary or fast,
            temperature=settings.llm_temperature_summary,
            max_tokens=settings.llm_max_tokens_summary,
            requires_json=False,
        ),
        Route.JUDGE: spec(
            Route.JUDGE,
            model=settings.llm_model_judge or smart,
            temperature=settings.llm_temperature_judge,
            max_tokens=settings.llm_max_tokens_judge,
            requires_json=True,
        ),
    }


def validate_routes(routes: dict[Route, RouteSpec]) -> RouteValidation:
    """Check route consistency and JSON support of known models."""
    errors: list[str] = []
    warnings: list[str] = []
    seen_models: set[str] = set()

    for route in routes.values():
        if not route.model.strip():
            errors.append(f"{route.name}: model is empty")
        if route.fallback and route.fallback == route.model:
            errors.append(f"{route.name}: fallback equals primary model")
        if route.max_tokens <= 0:
            errors.append(f"{route.name}: max_tokens must be positive")
        if not 0.0 <= route.temperature <= 2.0:
            errors.append(f"{route.name}: temperature must be between 0 and 2")

        for model in (route.model, route.fallback):
            if not model or model in seen_models:
                continue
            seen_models.add(model)
            spec = CATALOG.get(model)
            if spec is None:
                warnings.append(f"unknown model: {model}")
            elif route.requires_json and not spec.json_schema:
                errors.append(f"{route.name}: {model} does not support JSON schema")

    return RouteValidation(errors=tuple(errors), warnings=tuple(warnings))
