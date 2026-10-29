"""OpenRouter adapter for the OpenAI-compatible chat completions API."""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from decimal import Decimal, InvalidOperation
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_fixed

from app.core.config import Settings
from app.llm.errors import (
    LLMBalanceExhausted,
    LLMError,
    LLMInvalidResponse,
    LLMRateLimited,
    LLMTimeout,
    LLMUnauthorized,
    LLMUnavailable,
)
from app.llm.port import LLMResponse, Message

SleepFn = Callable[[float], Awaitable[None]]

_RETRYABLE_EXCEPTIONS = (LLMTimeout, LLMRateLimited, LLMUnavailable)


class OpenRouterClient:
    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: SleepFn = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._attempts = settings.llm_max_retries + 1
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{settings.llm_base_url.rstrip('/')}/",
            timeout=settings.llm_timeout_seconds,
            headers={
                "Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}",
                "X-Title": "tg-bot-eng-teacher",
            },
        )
        self._default_model = settings.llm_model_fast

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        payload: dict[str, Any] = {
            "model": model or self._default_model,
            "messages": [
                {"role": message.role, "content": message.content} for message in messages
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "usage": {"include": True},
        }
        if response_schema is not None:
            payload["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": response_schema.__name__,
                    "strict": True,
                    "schema": response_schema.model_json_schema(),
                },
            }

        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=wait_fixed(self._settings.llm_retry_backoff_seconds),
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            reraise=True,
            sleep=self._sleep,
        ):
            with attempt:
                return await self._post(payload, response_schema)

        raise LLMUnavailable("LLM retry loop exhausted")

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _post(
        self,
        payload: dict[str, Any],
        response_schema: type[BaseModel] | None,
    ) -> LLMResponse:
        started = time.perf_counter()
        try:
            response = await self._client.post("chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeout("LLM request timed out") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable("LLM transport error") from exc

        if response.status_code != httpx.codes.OK:
            self._raise_for_status(response.status_code)

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._parse(response, str(payload["model"]), latency_ms, response_schema)

    def _raise_for_status(self, status_code: int) -> None:
        if status_code in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise LLMUnauthorized("LLM credentials are invalid")
        if status_code == httpx.codes.PAYMENT_REQUIRED:
            raise LLMBalanceExhausted("LLM provider balance is exhausted")
        if status_code == httpx.codes.TOO_MANY_REQUESTS:
            raise LLMRateLimited("LLM provider rate limit reached")
        if status_code >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise LLMUnavailable(f"LLM provider returned {status_code}")
        raise LLMError(f"LLM request failed with status {status_code}")

    def _parse(
        self,
        response: httpx.Response,
        model: str,
        latency_ms: int,
        response_schema: type[BaseModel] | None,
    ) -> LLMResponse:
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMInvalidResponse("LLM response is not valid JSON") from exc

        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise LLMInvalidResponse("LLM response has no choices")

        message = choices[0].get("message") or {}
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise LLMInvalidResponse("LLM response has empty content")

        usage = data.get("usage") or {}
        parsed = self._parse_structured(content, response_schema) if response_schema else None

        return LLMResponse(
            content=content,
            parsed=parsed,
            model=str(data.get("model") or model),
            input_tokens=_as_int(usage.get("prompt_tokens")),
            output_tokens=_as_int(usage.get("completion_tokens")),
            cost_usd=_as_decimal(usage.get("cost")),
            latency_ms=latency_ms,
        )

    def _parse_structured(
        self,
        content: str,
        response_schema: type[BaseModel],
    ) -> dict[str, Any]:
        try:
            raw = json.loads(content)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponse("structured response is not valid JSON") from exc
        if not isinstance(raw, dict):
            raise LLMInvalidResponse("structured response must be a JSON object")
        try:
            response_schema.model_validate(raw)
        except ValidationError as exc:
            raise LLMInvalidResponse("structured response does not match schema") from exc
        return raw


def _as_int(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _as_decimal(value: Any) -> Decimal:
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
