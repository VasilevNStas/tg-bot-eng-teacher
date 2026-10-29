"""Provider-agnostic LLM contract."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Literal, Protocol

from pydantic import BaseModel

Role = Literal["system", "user", "assistant"]


@dataclass(frozen=True, slots=True)
class Message:
    role: Role
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
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
