"""Deterministic replay LLM used by the offline eval suite."""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any

from pydantic import BaseModel

from app.evals.models import EvalCase
from app.llm.models import RouteSpec
from app.llm.port import LLMResponse
from app.llm.port import Message as LlmMessage
from app.llm.pricing import estimate_cost_usd
from app.llm.tokens import estimate_tokens
from app.services.prompts.builder import BuiltPrompt


def build_replay_response(
    case: EvalCase,
    prompt: BuiltPrompt,
    route: RouteSpec,
) -> LLMResponse:
    """Turn a case fixture into the exact provider response shape."""
    if case.structured:
        payload: dict[str, Any] = dict(case.replay)  # type: ignore[arg-type]
        content = json.dumps(payload, ensure_ascii=False)
        parsed: dict[str, Any] | None = payload
    else:
        content = str(case.replay)
        parsed = None

    input_tokens = prompt.estimated_input_tokens
    output_tokens = estimate_tokens(content)
    return LLMResponse(
        content=content,
        parsed=parsed,
        model=route.model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=estimate_cost_usd(route.model, input_tokens, output_tokens),
        latency_ms=0,
    )


class ReplayLLM:
    """Return one pre-built response and record how many times it was called."""

    def __init__(self, response: LLMResponse) -> None:
        self._response = response
        self.calls = 0

    async def complete(
        self,
        messages: Sequence[LlmMessage],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        self.calls += 1
        return self._response

    async def aclose(self) -> None:
        return None
