from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from pathlib import Path

from app.db.enums import CEFRLevel, ConversationMode, ErrorCategory
from app.evals.loader import load_cases
from app.evals.models import EvalCase, EvalExpectation, ExpectedCorrection
from app.evals.rubric import CaseResult, evaluate_case
from app.evals.runner import (
    EvalRun,
    compare_baseline,
    evaluate_cases,
    run_evals,
)
from app.llm.port import LLMResponse

CASES_DIR = Path("evals/cases")
BASELINE = Path("evals/baseline.json")


def test_load_all_cases() -> None:
    cases = load_cases(CASES_DIR)

    assert len(cases) == 24
    assert {case.suite for case in cases} == {"chat", "correction", "roleplay"}
    assert len({case.id for case in cases}) == 24
    for suite in ("chat", "correction", "roleplay"):
        assert sum(1 for case in cases if case.suite == suite) == 8
    for case in cases:
        if case.suite == "roleplay":
            assert case.scenario is not None
        assert isinstance(case.replay, dict)


def test_load_cases_filtered_by_suite() -> None:
    cases = load_cases(CASES_DIR, suite="chat")

    assert len(cases) == 8
    assert all(case.suite == "chat" for case in cases)


async def test_replay_eval_matches_baseline() -> None:
    cases = load_cases(CASES_DIR)
    run = await evaluate_cases(cases)
    baseline = json.loads(await asyncio.to_thread(BASELINE.read_text, encoding="utf-8"))

    assert run.passed
    assert run.passed_count == 24
    assert run.score == 1.0
    assert compare_baseline(run, baseline) == ((), ())


async def test_missing_expected_correction_fails_rubric() -> None:
    case = EvalCase(
        id="case",
        suite="correction",
        level=CEFRLevel.A2,
        input="I am go to school.",
        scenario=None,
        replay={
            "reply": "Good try!",
            "corrections": [],
            "new_vocabulary": [],
        },
        expectation=EvalExpectation(
            corrections=(ExpectedCorrection("am go", "go", ErrorCategory.TENSE),),
        ),
    )
    response = LLMResponse(
        content='{"reply":"Good try!","corrections":[],"new_vocabulary":[]}',
        parsed={"reply": "Good try!", "corrections": [], "new_vocabulary": []},
        model="fake/model",
        input_tokens=1,
        output_tokens=1,
        cost_usd=Decimal("0"),
        latency_ms=0,
    )

    result = evaluate_case(case, response, prompt_version="v1")

    assert not result.passed
    assert "corrections_expected" in {check.name for check in result.failed_checks}


def test_prompt_version_change_is_a_baseline_error() -> None:
    result = CaseResult("case", "chat", "v2", True, ())
    run = EvalRun("replay", "chat", (result,))
    baseline = {
        "cases": {
            "case": {"suite": "chat", "prompt_version": "v1", "passed": True},
        }
    }

    errors, warnings = compare_baseline(run, baseline)

    assert any("prompt version changed" in error for error in errors)
    assert warnings == ()


def test_regression_is_a_baseline_error() -> None:
    result = CaseResult("case", "chat", "v1", False, ())
    run = EvalRun("replay", "chat", (result,))
    baseline = {
        "cases": {
            "case": {"suite": "chat", "prompt_version": "v1", "passed": True},
        }
    }

    errors, _ = compare_baseline(run, baseline)

    assert "regression: case" in errors


async def test_cli_eval_updates_and_compares_baseline(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"

    assert (
        await run_evals(
            cases_dir=CASES_DIR,
            baseline_path=baseline,
            update_baseline=True,
            as_json=True,
        )
        == 0
    )
    assert baseline.is_file()
    assert (
        await run_evals(
            cases_dir=CASES_DIR,
            baseline_path=baseline,
            as_json=True,
        )
        == 0
    )


async def test_eval_loader_is_deterministic() -> None:
    first = load_cases(CASES_DIR)
    second = load_cases(CASES_DIR)

    assert [case.id for case in first] == [case.id for case in second]
    assert all(case.mode is not ConversationMode.DRILL for case in first)
