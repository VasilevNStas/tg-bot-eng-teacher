"""Provider-agnostic LLM contract.

Services depend on ``LLMClient`` only, so the OpenRouter adapter can be swapped
for a fake in tests or another provider without touching business logic.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    """One chat message in the OpenAI-compatible role/content format."""

    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Normalized completion, including usage and provider-reported cost."""

    content: str
    parsed: dict[str, Any] | None
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: Decimal
    latency_ms: int

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


class LLMClient(Protocol):
    """Minimal async client surface used by the application."""

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse: ...

    async def aclose(self) -> None: ...
