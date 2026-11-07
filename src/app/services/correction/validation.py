"""Post-validation for structured LLM corrections.

The JSON schema keeps the shape valid, but a model can still invent fragments
that never appeared in the learner's message or return an unbounded list. This
module applies the cheap semantic checks described in ADR-052 before anything
is shown or persisted.
"""

from __future__ import annotations

from app.services.correction.schema import CorrectionPayload

MAX_CORRECTIONS = 5
MAX_ROLEPLAY_CORRECTIONS = 2
MAX_VOCABULARY = 3
MAX_REPLY_CHARS = 4096
MAX_EXPLANATION_CHARS = 300
MAX_TERM_CHARS = 128
MAX_TRANSLATION_CHARS = 512
MAX_CONTEXT_CHARS = 500


def sanitize_payload(
    payload: CorrectionPayload,
    user_text: str,
    *,
    max_corrections: int = MAX_CORRECTIONS,
) -> CorrectionPayload:
    """Drop invented corrections and clamp every free-text field.

    ``original`` must occur in the learner's message (case/whitespace
    insensitive); otherwise the correction is not trustworthy and is removed.
    ``max_corrections`` lets role-play keep its feedback compact (1-2 items).
    """
    haystack = _normalize(user_text)
    corrections = []
    for item in payload.corrections:
        if _normalize(item.original) not in haystack:
            continue
        corrections.append(
            item.model_copy(
                update={
                    "original": item.original[:MAX_CONTEXT_CHARS],
                    "corrected": item.corrected[:MAX_CONTEXT_CHARS],
                    "explanation_ru": item.explanation_ru[:MAX_EXPLANATION_CHARS],
                }
            )
        )
        if len(corrections) >= max_corrections:
            break

    vocabulary = [
        item.model_copy(
            update={
                "term": item.term[:MAX_TERM_CHARS],
                "translation_ru": item.translation_ru[:MAX_TRANSLATION_CHARS],
                "context": item.context[:MAX_CONTEXT_CHARS],
            }
        )
        for item in payload.new_vocabulary[:MAX_VOCABULARY]
    ]

    return payload.model_copy(
        update={
            "reply": payload.reply[:MAX_REPLY_CHARS],
            "corrections": corrections,
            "new_vocabulary": vocabulary,
        }
    )


def _normalize(text: str) -> str:
    """Casefold and collapse whitespace for a conservative substring check."""
    return " ".join(text.casefold().split())
