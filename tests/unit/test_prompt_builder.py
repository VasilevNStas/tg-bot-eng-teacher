from __future__ import annotations

from pathlib import Path

import pytest

from app.db.enums import CEFRLevel, ConversationMode
from app.llm.port import Message
from app.services.prompts.builder import PromptBuilder, PromptContext
from app.services.prompts.errors import PromptTooLong
from app.services.prompts.registry import PromptRegistry

PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"


def make_builder(*, max_input_tokens: int = 8000, history_window: int = 12) -> PromptBuilder:
    return PromptBuilder(
        PromptRegistry(PROMPTS_DIR),
        max_input_tokens=max_input_tokens,
        history_window=history_window,
    )


def make_context(**overrides: object) -> PromptContext:
    values: dict[str, object] = {
        "level": CEFRLevel.B1,
        "mode": ConversationMode.CHAT,
        "user_input": "Hello, I want to practice.",
    }
    values.update(overrides)
    return PromptContext(**values)  # type: ignore[arg-type]


def test_builds_persona_level_and_mode() -> None:
    prompt = make_builder().build(make_context())

    system = prompt.messages[0].content
    assert "Alex" in system
    assert "B1" in system
    assert "free conversation" in system.lower()
    assert prompt.messages[-1] == Message(role="user", content="Hello, I want to practice.")


def test_profile_is_included_as_json() -> None:
    prompt = make_builder().build(make_context(profile={"goal": "travel", "level": "B1"}))

    profile_messages = [m for m in prompt.messages if "Learner profile" in m.content]
    assert len(profile_messages) == 1
    assert '"goal":"travel"' in profile_messages[0].content


def test_summary_is_included() -> None:
    prompt = make_builder().build(make_context(summary="Learner talked about a trip to London."))

    assert any("trip to London" in m.content for m in prompt.messages)


def test_history_is_limited_by_window() -> None:
    history = [
        Message(role="user", content="first"),
        Message(role="assistant", content="second"),
        Message(role="user", content="third"),
    ]

    prompt = make_builder(history_window=2).build(make_context(history=history))

    conversation = [m for m in prompt.messages if m.role != "system"]
    assert [m.content for m in conversation] == ["second", "third", "Hello, I want to practice."]


def test_zero_history_window_drops_history() -> None:
    history = [Message(role="user", content="old message")]

    prompt = make_builder(history_window=0).build(make_context(history=history))

    conversation = [m for m in prompt.messages if m.role != "system"]
    assert conversation == [Message(role="user", content="Hello, I want to practice.")]


def test_history_is_trimmed_to_fit_budget() -> None:
    history = [
        Message(role="user", content=f"message {index} " + "filler " * 200) for index in range(20)
    ]

    prompt = make_builder(max_input_tokens=700).build(make_context(history=history))

    assert prompt.estimated_input_tokens <= 700
    conversation = [m for m in prompt.messages if m.role != "system"]
    assert conversation == [Message(role="user", content="Hello, I want to practice.")]


def test_raises_when_budget_cannot_fit_system_prompt() -> None:
    with pytest.raises(PromptTooLong):
        make_builder(max_input_tokens=10).build(make_context())


def test_version_depends_on_level_and_mode() -> None:
    builder = make_builder()
    base = builder.build(make_context())

    other_level = builder.build(make_context(level=CEFRLevel.C1))
    other_mode = builder.build(make_context(mode=ConversationMode.CORRECTION))

    assert base.prompt_version != other_level.prompt_version
    assert base.prompt_version != other_mode.prompt_version
    assert len(base.prompt_version) == 12


def test_non_russian_native_language_is_declared() -> None:
    prompt = make_builder().build(make_context(native_language="de"))

    assert "Explain in de." in prompt.messages[0].content
