from __future__ import annotations

import re
import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from app.grading.normalize import normalize_answer

CANONICAL = re.compile(r"[\w\s'-]*", re.UNICODE)


def test_normalize_strips_and_collapses() -> None:
    assert normalize_answer("  Hello   world  ") == "hello world"
    assert normalize_answer("\n\thello\t\n") == "hello"


def test_normalize_handles_typographic_characters() -> None:
    assert normalize_answer("«Hello» — world…") == "hello - world"
    assert normalize_answer("I don’t know") == "i do not know"
    assert normalize_answer("café") == "café"


@given(st.text())
def test_normalize_is_idempotent(value: str) -> None:
    once = normalize_answer(value)
    assert normalize_answer(once) == once


@given(st.text())
def test_normalize_is_nfkc_stable(value: str) -> None:
    assert normalize_answer(unicodedata.normalize("NFKC", value)) == normalize_answer(value)


@given(st.text())
def test_normalize_returns_canonical_form(value: str) -> None:
    result = normalize_answer(value)
    assert result == result.strip()
    assert not result.startswith(("-", "'"))
    assert not result.endswith(("-", "'"))
    assert CANONICAL.fullmatch(result) is not None
    assert "  " not in result
