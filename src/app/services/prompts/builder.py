"""Assembles the LLM message list within an explicit token budget."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from app.db.enums import CEFRLevel, ConversationMode
from app.llm.port import Message
from app.llm.tokens import estimate_messages_tokens
from app.services.prompts.errors import PromptTooLong
from app.services.prompts.registry import PromptRegistry


@dataclass(frozen=True, slots=True)
class PromptContext:
    level: CEFRLevel
    mode: ConversationMode
    user_input: str
    profile: Mapping[str, object] = field(default_factory=dict)
    summary: str | None = None
    history: Sequence[Message] = field(default_factory=tuple)
    native_language: str = "ru"


@dataclass(frozen=True, slots=True)
class BuiltPrompt:
    messages: list[Message]
    prompt_version: str
    estimated_input_tokens: int


class PromptBuilder:
    def __init__(
        self,
        registry: PromptRegistry,
        *,
        max_input_tokens: int,
        history_window: int = 12,
    ) -> None:
        if max_input_tokens <= 0:
            raise ValueError("max_input_tokens must be positive")
        if history_window < 0:
            raise ValueError("history_window must not be negative")
        self._registry = registry
        self._max_input_tokens = max_input_tokens
        self._history_window = history_window

    @property
    def history_window(self) -> int:
        return self._history_window

    def build(self, context: PromptContext) -> BuiltPrompt:
        persona = self._registry.persona()
        level = self._registry.level(context.level)
        mode = self._registry.mode(context.mode)

        system = "\n\n".join((persona.content, level.content, mode.content))
        if context.native_language != "ru":
            system = f"{system}\n\n# Language\nExplain in {context.native_language}."

        messages = [Message(role="system", content=system)]
        profile_block = self._profile_block(context.profile)
        if profile_block is not None:
            messages.append(Message(role="system", content=profile_block))

        summary_message = self._summary_message(context.summary)
        if summary_message is not None:
            messages.append(summary_message)

        history = list(context.history[-self._history_window :]) if self._history_window else []
        user_message = Message(role="user", content=context.user_input)

        while (
            estimate_messages_tokens([*messages, *history, user_message]) > self._max_input_tokens
        ):
            if history:
                history.pop(0)
            elif summary_message is not None:
                messages.remove(summary_message)
                summary_message = None
            else:
                raise PromptTooLong(
                    f"prompt exceeds input budget of {self._max_input_tokens} tokens"
                )

        messages.extend(history)
        messages.append(user_message)

        return BuiltPrompt(
            messages=messages,
            prompt_version=_combined_version(persona.version, level.version, mode.version),
            estimated_input_tokens=estimate_messages_tokens(messages),
        )

    @staticmethod
    def _profile_block(profile: Mapping[str, object]) -> str | None:
        if not profile:
            return None
        payload = json.dumps(
            profile,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return f"Learner profile (JSON):\n{payload}"

    @staticmethod
    def _summary_message(summary: str | None) -> Message | None:
        if summary is None or not summary.strip():
            return None
        return Message(role="system", content=f"Conversation summary:\n{summary.strip()}")


def _combined_version(*versions: str) -> str:
    payload = ":".join(versions).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()[:12]
