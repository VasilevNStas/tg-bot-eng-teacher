"""Conservative token estimation for pre-flight context budgeting.

Provider-reported usage is authoritative; this estimator is only used to keep
a request under the input limit before it is sent.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.llm.port import Message

DEFAULT_MARGIN = 8


def estimate_tokens(text: str) -> int:
    if not text:
        return 0
    return max(1, len(text) // 3 + DEFAULT_MARGIN)


def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    return sum(estimate_tokens(message.content) for message in messages)
