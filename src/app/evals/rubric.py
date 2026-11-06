"""Deterministic rubric checks for golden LLM responses."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from decimal import Decimal

from app.evals.models import EvalCase, ExpectedCorrection
from app.llm.port import LLMResponse
from app.services.correction.schema import CorrectionItem, CorrectionPayload, NewVocabularyItem
from app.services.correction.validation import sanitize_payload

_CYRILLIC = re.compile(r"[\u0400-\u04FF]")
_LATIN = re.compile(r"[A-Za-z]")


@dataclass(frozen=True, slots=True)
class CheckResult:
    """One named deterministic rubric check."""

    name: str
    passed: bool
    detail: str = ""

    def as_dict(self) -> dict[str, object]:
        return {"name": self.name, "passed": self.passed, "detail": self.detail}


@dataclass(frozen=True, slots=True)
class CaseResult:
    """Rubric outcome for one golden case."""

    case_id: str
    suite: str
    prompt_version: str
    passed: bool
    checks: tuple[CheckResult, ...]
    cost_usd: Decimal = Decimal("0")

    @property
    def failed_checks(self) -> tuple[CheckResult, ...]:
        return tuple(check for check in self.checks if not check.passed)

    def as_dict(self) -> dict[str, object]:
        return {
            "case_id": self.case_id,
            "suite": self.suite,
            "prompt_version": self.prompt_version,
            "passed": self.passed,
            "cost_usd": str(self.cost_usd),
            "checks": [check.as_dict() for check in self.checks],
        }


def evaluate_case(
    case: EvalCase,
    response: LLMResponse,
    *,
    prompt_version: str,
) -> CaseResult:
    """Run the case rubric and return a structured result."""
    checks: list[CheckResult] = []
    if case.structured:
        try:
            raw = _validate_structured(response)
        except ValueError as exc:
            return CaseResult(
                case_id=case.id,
                suite=case.suite,
                prompt_version=prompt_version,
                passed=False,
                checks=(CheckResult("schema", False, str(exc)),),
                cost_usd=response.cost_usd,
            )
        sanitized = sanitize_payload(raw, case.input)
        reply = raw.reply
        raw_corrections = tuple(raw.corrections)
        corrections = tuple(sanitized.corrections)
        vocabulary = tuple(sanitized.new_vocabulary)
    else:
        reply = response.content
        raw_corrections = ()
        corrections = ()
        vocabulary = ()

    checks.append(_check_reply_present(reply))
    checks.append(_check_reply_language(reply, case.expectation.reply_language))
    checks.append(_check_reply_words(reply, case))
    checks.append(_check_must_contain(reply, case.expectation.must_contain))
    checks.append(_check_must_not_contain(reply, case.expectation.must_not_contain))
    if case.structured:
        checks.append(_check_correction_count(raw_corrections, case.expectation.max_corrections))
        checks.append(_check_correction_originals(raw_corrections, case.input))
        checks.append(_check_expected_corrections(corrections, case.expectation.corrections))
        checks.append(
            _check_russian_explanations(
                raw_corrections,
                enabled=case.expectation.require_russian_explanations,
            )
        )
        checks.append(_check_vocabulary(vocabulary, case.expectation.vocabulary))

    return CaseResult(
        case_id=case.id,
        suite=case.suite,
        prompt_version=prompt_version,
        passed=all(check.passed for check in checks),
        checks=tuple(checks),
        cost_usd=response.cost_usd,
    )


def _validate_structured(response: LLMResponse) -> CorrectionPayload:
    raw = response.parsed
    if raw is None:
        try:
            raw = json.loads(response.content)
        except json.JSONDecodeError as exc:
            raise ValueError(f"response is not valid JSON: {exc}") from exc
    return CorrectionPayload.model_validate(raw)


def _check_reply_present(reply: str) -> CheckResult:
    return CheckResult("reply_present", bool(reply.strip()))


def _check_reply_language(reply: str, expected: str) -> CheckResult:
    if expected == "en":
        passed = bool(_LATIN.search(reply)) and not _CYRILLIC.search(reply)
    elif expected == "ru":
        passed = bool(_CYRILLIC.search(reply)) and not _LATIN.search(reply)
    else:
        return CheckResult("reply_language", False, f"unknown language: {expected}")
    return CheckResult("reply_language", passed, f"expected {expected}")


def _check_reply_words(reply: str, case: EvalCase) -> CheckResult:
    count = len(reply.split())
    low = case.expectation.min_reply_words
    high = case.expectation.max_reply_words
    passed = low <= count <= high
    return CheckResult("reply_words", passed, f"{count} words, expected {low}..{high}")


def _check_must_contain(reply: str, phrases: tuple[str, ...]) -> CheckResult:
    folded = reply.casefold()
    missing = [phrase for phrase in phrases if phrase.casefold() not in folded]
    return CheckResult(
        "must_contain", not missing, "missing: " + ", ".join(missing) if missing else ""
    )


def _check_must_not_contain(reply: str, phrases: tuple[str, ...]) -> CheckResult:
    folded = reply.casefold()
    found = [phrase for phrase in phrases if phrase.casefold() in folded]
    return CheckResult(
        "must_not_contain",
        not found,
        "found: " + ", ".join(found) if found else "",
    )


def _check_correction_count(
    corrections: tuple[CorrectionItem, ...],
    maximum: int,
) -> CheckResult:
    return CheckResult(
        "corrections_max",
        len(corrections) <= maximum,
        f"{len(corrections)} corrections, max {maximum}",
    )


def _check_correction_originals(
    corrections: tuple[CorrectionItem, ...],
    user_text: str,
) -> CheckResult:
    haystack = _normalize(user_text)
    invented = [item.original for item in corrections if _normalize(item.original) not in haystack]
    return CheckResult(
        "corrections_original",
        not invented,
        "invented: " + ", ".join(invented) if invented else "",
    )


def _check_expected_corrections(
    corrections: tuple[CorrectionItem, ...],
    expected: tuple[ExpectedCorrection, ...],
) -> CheckResult:
    missing: list[str] = []
    for item in expected:
        if not any(_matches(item, actual) for actual in corrections):
            missing.append(f"{item.original} -> {item.corrected}")
    return CheckResult(
        "corrections_expected",
        not missing,
        "missing: " + "; ".join(missing) if missing else "",
    )


def _check_russian_explanations(
    corrections: tuple[CorrectionItem, ...],
    *,
    enabled: bool,
) -> CheckResult:
    if not enabled:
        return CheckResult("explanations_russian", True, "not required")
    bad = [item.original for item in corrections if not _CYRILLIC.search(item.explanation_ru)]
    return CheckResult(
        "explanations_russian",
        not bad,
        "non-Russian: " + ", ".join(bad) if bad else "",
    )


def _check_vocabulary(
    vocabulary: tuple[NewVocabularyItem, ...],
    expected: tuple[str, ...],
) -> CheckResult:
    known = {_normalize(item.term) for item in vocabulary}
    missing = [term for term in expected if _normalize(term) not in known]
    return CheckResult(
        "vocabulary_expected",
        not missing,
        "missing: " + ", ".join(missing) if missing else "",
    )


def _matches(expected: ExpectedCorrection, actual: CorrectionItem) -> bool:
    if _normalize(expected.original) != _normalize(actual.original):
        return False
    if _normalize(expected.corrected) != _normalize(actual.corrected):
        return False
    return expected.category is None or expected.category == actual.category


def _normalize(text: str) -> str:
    return " ".join(text.casefold().split())
