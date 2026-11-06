"""Load and validate YAML golden-eval cases."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from app.db.enums import CEFRLevel, ErrorCategory
from app.evals.errors import EvalError
from app.evals.models import (
    DEFAULT_FORBIDDEN_PHRASES,
    LEVEL_REPLY_WORD_CAPS,
    SUITES,
    EvalCase,
    EvalExpectation,
    ExpectedCorrection,
)
from app.services.correction.schema import CorrectionPayload
from app.services.prompts.registry import SCENARIO_SLUG


def load_cases(cases_dir: Path, *, suite: str = "all") -> tuple[EvalCase, ...]:
    """Load all case files, optionally filtered to one suite."""
    if suite != "all" and suite not in SUITES:
        raise EvalError(f"unknown suite: {suite!r}")
    if not cases_dir.is_dir():
        raise EvalError(f"eval cases directory not found: {cases_dir}")

    cases: list[EvalCase] = []
    seen: set[str] = set()
    for path in sorted(cases_dir.glob("*.yaml")):
        data = _load_yaml(path)
        file_suite = str(data.get("suite", path.stem)).strip()
        if file_suite not in SUITES:
            raise EvalError(f"{path}: unknown suite {file_suite!r}")
        if suite != "all" and file_suite != suite:
            continue
        raw_cases = data.get("cases")
        if not isinstance(raw_cases, list) or not raw_cases:
            raise EvalError(f"{path}: cases must be a non-empty list")
        for raw in raw_cases:
            if not isinstance(raw, Mapping):
                raise EvalError(f"{path}: every case must be a mapping")
            case = _parse_case(raw, file_suite, path)
            if case.id in seen:
                raise EvalError(f"duplicate eval case id: {case.id}")
            seen.add(case.id)
            cases.append(case)

    if not cases:
        raise EvalError(f"no eval cases found in {cases_dir}")
    return tuple(cases)


def _load_yaml(path: Path) -> Mapping[str, Any]:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise EvalError(f"{path}: invalid YAML: {exc}") from exc
    if not isinstance(data, Mapping):
        raise EvalError(f"{path}: top-level YAML must be a mapping")
    return data


def _parse_case(raw: Mapping[str, Any], suite: str, path: Path) -> EvalCase:
    case_id = str(raw.get("id", "")).strip()
    if not case_id:
        raise EvalError(f"{path}: case id is required")

    level_raw = str(raw.get("level", "")).strip().upper()
    try:
        level = CEFRLevel(level_raw)
    except ValueError as exc:
        raise EvalError(f"{path}/{case_id}: unknown level {level_raw!r}") from exc

    text = str(raw.get("input", "")).strip()
    if not text:
        raise EvalError(f"{path}/{case_id}: input is required")

    scenario_raw = raw.get("scenario")
    scenario = str(scenario_raw).strip() if scenario_raw is not None else None
    if suite == "roleplay":
        if scenario is None or not SCENARIO_SLUG.fullmatch(scenario):
            raise EvalError(f"{path}/{case_id}: roleplay needs a valid scenario slug")
    elif scenario is not None:
        raise EvalError(f"{path}/{case_id}: scenario is only valid for roleplay")

    replay = _parse_replay(raw.get("replay"), suite, path, case_id)
    expectation = _parse_expectation(raw.get("expect"), suite, level, text, path, case_id)
    return EvalCase(
        id=case_id,
        suite=suite,
        level=level,
        input=text,
        scenario=scenario,
        replay=replay,
        expectation=expectation,
    )


def _parse_replay(
    value: Any,
    suite: str,
    path: Path,
    case_id: str,
) -> Mapping[str, object] | str:
    if suite == "roleplay":
        if not isinstance(value, str) or not value.strip():
            raise EvalError(f"{path}/{case_id}: roleplay replay must be non-empty text")
        return value.strip()

    if not isinstance(value, Mapping):
        raise EvalError(f"{path}/{case_id}: structured replay must be a mapping")
    try:
        CorrectionPayload.model_validate(value)
    except ValueError as exc:
        raise EvalError(f"{path}/{case_id}: invalid structured replay: {exc}") from exc
    return value


def _parse_expectation(
    value: Any,
    suite: str,
    level: CEFRLevel,
    text: str,
    path: Path,
    case_id: str,
) -> EvalExpectation:
    raw = value if isinstance(value, Mapping) else {}
    forbidden = tuple(
        str(item).casefold() for item in raw.get("must_not_contain", DEFAULT_FORBIDDEN_PHRASES)
    )
    corrections = _parse_expected_corrections(raw.get("corrections"), path, case_id)
    for item in corrections:
        if _normalize(item.original) not in _normalize(text):
            raise EvalError(
                f"{path}/{case_id}: expected original is not in input: {item.original!r}"
            )

    max_corrections = int(raw.get("max_corrections", 3 if suite != "roleplay" else 0))
    if max_corrections < len(corrections):
        raise EvalError(f"{path}/{case_id}: max_corrections is lower than expected corrections")

    return EvalExpectation(
        reply_language=str(raw.get("reply_language", "en")),
        min_reply_words=int(raw.get("min_reply_words", 1)),
        max_reply_words=int(raw.get("max_reply_words", LEVEL_REPLY_WORD_CAPS[level])),
        must_contain=tuple(str(item) for item in raw.get("must_contain", ())),
        must_not_contain=forbidden,
        corrections=corrections,
        max_corrections=max_corrections,
        require_russian_explanations=bool(
            raw.get("require_russian_explanations", suite != "roleplay")
        ),
        vocabulary=tuple(str(item) for item in raw.get("vocabulary", ())),
    )


def _parse_expected_corrections(
    value: Any,
    path: Path,
    case_id: str,
) -> tuple[ExpectedCorrection, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EvalError(f"{path}/{case_id}: expect.corrections must be a list")

    parsed: list[ExpectedCorrection] = []
    for raw in value:
        if not isinstance(raw, Mapping):
            raise EvalError(f"{path}/{case_id}: correction expectation must be a mapping")
        original = str(raw.get("original", "")).strip()
        corrected = str(raw.get("corrected", "")).strip()
        if not original or not corrected:
            raise EvalError(f"{path}/{case_id}: correction needs original and corrected")
        category_raw = raw.get("category")
        category = None
        if category_raw is not None:
            try:
                category = ErrorCategory(str(category_raw))
            except ValueError as exc:
                raise EvalError(f"{path}/{case_id}: unknown category {category_raw!r}") from exc
        parsed.append(ExpectedCorrection(original, corrected, category))
    return tuple(parsed)


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())
