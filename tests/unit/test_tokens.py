from __future__ import annotations

from app.llm.port import Message
from app.llm.tokens import estimate_messages_tokens, estimate_tokens


def test_empty_text_has_no_tokens() -> None:
    assert estimate_tokens("") == 0


def test_estimator_is_conservative_and_positive() -> None:
    assert estimate_tokens("hello") > 0
    assert estimate_tokens("hello world") >= estimate_tokens("hello")


def test_messages_are_summed() -> None:
    messages = [
        Message(role="system", content="You are a tutor."),
        Message(role="user", content="Hello"),
    ]

    assert estimate_messages_tokens(messages) == sum(
        estimate_tokens(message.content) for message in messages
    )
