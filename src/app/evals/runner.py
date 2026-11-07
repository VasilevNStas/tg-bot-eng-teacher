"""Golden-dialogue eval runner with replay, live mode and baseline compare."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from app.core.config import Settings, get_settings
from app.evals.errors import EvalError
from app.evals.loader import load_cases
from app.evals.models import EvalCase
from app.evals.replay import ReplayLLM, build_replay_response
from app.evals.rubric import CaseResult, evaluate_case
from app.llm.models import Route, RouteSpec, build_routes
from app.llm.openrouter import OpenRouterClient
from app.llm.port import LLMResponse
from app.llm.pricing import estimate_cost_usd, estimate_route_cost_usd
from app.services.correction.schema import CorrectionPayload
from app.services.prompts.builder import BuiltPrompt, PromptBuilder, PromptContext
from app.services.prompts.registry import PromptRegistry

DEFAULT_CASES_DIR = Path("evals/cases")
DEFAULT_BASELINE = Path("evals/baseline.json")


@dataclass(frozen=True, slots=True)
class EvalRun:
    """All case results for one suite/mode selection."""

    mode: str
    suite: str
    results: tuple[CaseResult, ...]
    live_cost_usd: Decimal = Decimal("0")

    @property
    def passed(self) -> bool:
        return all(result.passed for result in self.results)

    @property
    def passed_count(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def score(self) -> float:
        return self.passed_count / len(self.results) if self.results else 0.0

    def summary_by_suite(self) -> dict[str, dict[str, int]]:
        summary: dict[str, dict[str, int]] = {}
        for result in self.results:
            bucket = summary.setdefault(result.suite, {"cases": 0, "passed": 0})
            bucket["cases"] += 1
            if result.passed:
                bucket["passed"] += 1
        return summary

    def as_dict(self) -> dict[str, object]:
        return {
            "mode": self.mode,
            "suite": self.suite,
            "cases": len(self.results),
            "passed": self.passed_count,
            "score": self.score,
            "live_cost_usd": str(self.live_cost_usd),
            "suites": self.summary_by_suite(),
            "results": [result.as_dict() for result in self.results],
        }


async def evaluate_cases(
    cases: Sequence[EvalCase],
    *,
    live: bool = False,
    max_cost_usd: Decimal = Decimal("0.05"),
    settings: Settings | None = None,
) -> EvalRun:
    """Run one case set through replay or the real provider."""
    if live and max_cost_usd <= 0:
        raise EvalError("max_cost_usd must be positive")

    prompts_dir = (
        settings.prompts_dir
        if live and settings is not None
        else Path(os.environ.get("PROMPTS_DIR", "./prompts"))
    )
    registry = PromptRegistry(prompts_dir)
    builder = PromptBuilder(registry, max_input_tokens=4000, history_window=0)

    client: OpenRouterClient | None = None
    if live:
        if settings is None:
            try:
                settings = get_settings()
            except ValidationError as exc:
                raise EvalError("live eval requires valid LLM settings") from exc
        routes = build_routes(settings)
        client = OpenRouterClient(settings)
    else:
        routes = _replay_routes()

    results: list[CaseResult] = []
    spent = Decimal("0")
    try:
        for case in cases:
            prompt = _build_prompt(builder, registry, case)
            route = routes[Route(case.suite)]
            if client is not None:
                response = await _live_complete(
                    client,
                    prompt,
                    route,
                    case,
                    spent=spent,
                    max_cost_usd=max_cost_usd,
                )
                spent += _actual_cost(
                    response.cost_usd, response.model, response.input_tokens, response.output_tokens
                )
            else:
                replay = ReplayLLM(build_replay_response(case, prompt, route))
                response = await replay.complete(
                    prompt.messages,
                    model=route.model,
                    temperature=route.temperature,
                    max_tokens=route.max_tokens,
                    response_schema=CorrectionPayload if case.structured else None,
                )
            results.append(evaluate_case(case, response, prompt_version=prompt.prompt_version))
    finally:
        if client is not None:
            await client.aclose()

    return EvalRun(
        mode="live" if live else "replay",
        suite=_suite_label(cases),
        results=tuple(results),
        live_cost_usd=spent,
    )


async def run_evals(
    *,
    cases_dir: Path = DEFAULT_CASES_DIR,
    suite: str = "all",
    baseline_path: Path = DEFAULT_BASELINE,
    live: bool = False,
    update_baseline: bool = False,
    max_cost_usd: Decimal = Decimal("0.05"),
    as_json: bool = False,
) -> int:
    """CLI entry point: load, run, compare baseline and print the report."""
    if live and update_baseline:
        raise EvalError("--update-baseline is only valid for replay runs")
    cases = load_cases(cases_dir, suite=suite)
    run = await evaluate_cases(cases, live=live, max_cost_usd=max_cost_usd)

    baseline_errors: tuple[str, ...] = ()
    baseline_warnings: tuple[str, ...] = ()
    if live:
        baseline_warnings = ("live run: baseline was not compared",)
    elif update_baseline:
        _write_baseline(baseline_path, run)
    else:
        baseline = _load_baseline(baseline_path)
        if baseline is None:
            baseline_errors = (f"baseline missing: {baseline_path}; run `eval --update-baseline`",)
        else:
            baseline_errors, baseline_warnings = compare_baseline(run, baseline)

    if as_json:
        payload = run.as_dict()
        payload["baseline"] = {
            "errors": list(baseline_errors),
            "warnings": list(baseline_warnings),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        _print_report(run, baseline_errors, baseline_warnings, baseline_path, update_baseline)

    if not run.passed or baseline_errors:
        return 1
    return 0


def compare_baseline(
    run: EvalRun,
    baseline: Mapping[str, Any],
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Compare current results with a baseline keyed by prompt version."""
    raw_cases = baseline.get("cases")
    if not isinstance(raw_cases, Mapping):
        return ("baseline has no cases mapping",), ()

    errors: list[str] = []
    warnings: list[str] = []
    for result in run.results:
        entry = raw_cases.get(result.case_id)
        if not isinstance(entry, Mapping):
            errors.append(f"new case without baseline: {result.case_id}")
            continue
        base_version = entry.get("prompt_version")
        if base_version != result.prompt_version:
            errors.append(
                f"prompt version changed for {result.case_id}: "
                f"{base_version} -> {result.prompt_version}; update baseline"
            )
        if bool(entry.get("passed")) and not result.passed:
            errors.append(f"regression: {result.case_id}")

    current_ids = {result.case_id for result in run.results}
    for case_id in raw_cases:
        if case_id not in current_ids and _baseline_entry_suite(raw_cases[case_id]) == run.suite:
            warnings.append(f"baseline case no longer present: {case_id}")
    return tuple(errors), tuple(warnings)


def _baseline_entry_suite(entry: object) -> str | None:
    if isinstance(entry, Mapping):
        value = entry.get("suite")
        return str(value) if value is not None else None
    return None


def _load_baseline(path: Path) -> Mapping[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise EvalError(f"{path}: invalid baseline JSON: {exc}") from exc
    if not isinstance(data, Mapping):
        raise EvalError(f"{path}: baseline must be a JSON object")
    return data


def _write_baseline(path: Path, run: EvalRun) -> None:
    existing = _load_baseline(path) or {}
    raw_cases = existing.get("cases")
    cases: dict[str, Any] = dict(raw_cases) if isinstance(raw_cases, Mapping) else {}
    for result in run.results:
        cases[result.case_id] = {
            "suite": result.suite,
            "prompt_version": result.prompt_version,
            "passed": result.passed,
            "failed_checks": [check.name for check in result.failed_checks],
        }

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "replay",
        "cases": dict(sorted(cases.items())),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _print_report(
    run: EvalRun,
    baseline_errors: tuple[str, ...],
    baseline_warnings: tuple[str, ...],
    baseline_path: Path,
    update_baseline: bool,
) -> None:
    print(
        f"mode={run.mode} suite={run.suite} cases={len(run.results)} "
        f"passed={run.passed_count} score={run.score:.3f}"
    )
    for suite, counts in sorted(run.summary_by_suite().items()):
        print(f"  {suite}: {counts['passed']}/{counts['cases']}")
    for result in run.results:
        if result.passed:
            continue
        details = "; ".join(
            f"{check.name} ({check.detail})" if check.detail else check.name
            for check in result.failed_checks
        )
        print(f"FAIL {result.case_id}: {details}")
    if run.mode == "live":
        print(f"live cost: ${run.live_cost_usd:.6f}")
    if update_baseline:
        print(f"baseline updated: {baseline_path}")
    else:
        for error in baseline_errors:
            print(f"baseline error: {error}")
        for warning in baseline_warnings:
            print(f"baseline warning: {warning}")
        if not baseline_errors and not baseline_warnings:
            print("baseline: ok")


def _build_prompt(
    builder: PromptBuilder,
    registry: PromptRegistry,
    case: EvalCase,
) -> BuiltPrompt:
    scenario = registry.scenario(case.scenario) if case.scenario is not None else None
    return builder.build(
        PromptContext(
            level=case.level,
            mode=case.mode,
            user_input=case.input,
            scenario=scenario,
        )
    )


async def _live_complete(
    client: OpenRouterClient,
    prompt: BuiltPrompt,
    route: RouteSpec,
    case: EvalCase,
    *,
    spent: Decimal,
    max_cost_usd: Decimal,
) -> LLMResponse:
    estimated = estimate_route_cost_usd(route, prompt.estimated_input_tokens)
    if spent + estimated > max_cost_usd:
        raise EvalError(
            f"live eval cost limit reached before {case.id}: "
            f"spent=${spent:.6f}, next<=${estimated:.6f}, limit=${max_cost_usd:.6f}"
        )
    kwargs: dict[str, Any] = {
        "model": route.model,
        "temperature": route.temperature,
        "max_tokens": route.max_tokens,
        "response_schema": CorrectionPayload if case.structured else None,
    }
    if route.fallback:
        kwargs["fallback_model"] = route.fallback
    return await client.complete(prompt.messages, **kwargs)


def _actual_cost(
    provider_cost: Decimal,
    model: str,
    input_tokens: int,
    output_tokens: int,
) -> Decimal:
    if provider_cost > 0:
        return provider_cost
    return estimate_cost_usd(model, input_tokens, output_tokens)


def _suite_label(cases: Sequence[EvalCase]) -> str:
    suites = {case.suite for case in cases}
    return suites.pop() if len(suites) == 1 else "all"


def _replay_routes() -> dict[Route, RouteSpec]:
    return {
        Route.CHAT: RouteSpec(Route.CHAT, "replay/chat", None, 0.7, 600, True),
        Route.CORRECTION: RouteSpec(Route.CORRECTION, "replay/correction", None, 0.3, 800, True),
        Route.ROLEPLAY: RouteSpec(Route.ROLEPLAY, "replay/roleplay", None, 0.8, 500, True),
        Route.SUMMARY: RouteSpec(Route.SUMMARY, "replay/summary", None, 0.2, 300, False),
        Route.JUDGE: RouteSpec(Route.JUDGE, "replay/judge", None, 0.0, 200, True),
    }
