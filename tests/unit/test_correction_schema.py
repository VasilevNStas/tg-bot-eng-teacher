from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.db.enums import ErrorCategory
from app.services.correction.schema import CorrectionPayload


def valid_payload() -> dict[str, Any]:
    return {
        "reply": "Good job! Where did you go?",
        "corrections": [
            {
                "original": "I go to cinema",
                "corrected": "I went to the cinema",
                "category": "tense",
                "explanation_ru": "Past Simple для завершённого действия.",
            }
        ],
        "new_vocabulary": [
            {
                "term": "cinema",
                "translation_ru": "кино",
                "context": "went to the cinema",
            }
        ],
    }


def test_valid_payload_round_trips() -> None:
    payload = CorrectionPayload.model_validate(valid_payload())

    dumped = payload.model_dump(mode="json")

    assert dumped["corrections"][0]["category"] == "tense"
    assert CorrectionPayload.model_validate(dumped) == payload


def test_unknown_category_is_rejected() -> None:
    data = valid_payload()
    data["corrections"][0]["category"] = "syntax"

    with pytest.raises(ValidationError):
        CorrectionPayload.model_validate(data)


def test_empty_lists_are_allowed() -> None:
    payload = CorrectionPayload.model_validate(
        {"reply": "Nice!", "corrections": [], "new_vocabulary": []}
    )

    assert payload.corrections == []
    assert payload.new_vocabulary == []


def test_schema_is_strict_and_uses_full_taxonomy() -> None:
    schema = CorrectionPayload.model_json_schema()

    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"reply", "corrections", "new_vocabulary"}
    assert _enum_values(schema, "ErrorCategory") == {category.value for category in ErrorCategory}


def _enum_values(node: Any, title: str) -> set[str]:
    if isinstance(node, dict):
        if node.get("title") == title and "enum" in node:
            return set(node["enum"])
        result: set[str] = set()
        for value in node.values():
            result |= _enum_values(value, title)
        return result
    if isinstance(node, list):
        result = set()
        for value in node:
            result |= _enum_values(value, title)
        return result
    return set()
