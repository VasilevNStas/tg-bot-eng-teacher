"""Conservative token estimation for pre-flight context budgeting.

Provider-reported usage is authoritative; this estimator is only used to keep
a request under the input limit before it is sent.
"""

from __future__ import annotations

from collections.abc import Sequence

from app.llm.port import Message

# Roughly 3 characters per token plus a safety margin. The estimate is
# intentionally pessimistic: overestimating only trims history, while
# underestimating can make the provider reject the request.
DEFAULT_MARGIN = 8


def estimate_tokens(text: str) -> int:
    """Estimate tokens for one string, never returning zero for non-empty text."""
    if not text:
        return 0
    return max(1, len(text) // 3 + DEFAULT_MARGIN)


def estimate_messages_tokens(messages: Sequence[Message]) -> int:
    """Estimate the total token cost of a message list."""
    return sum(estimate_tokens(message.content) for message in messages)
