"""OpenRouter adapter for the OpenAI-compatible chat completions API.

The adapter owns transport concerns: building the request, retrying transient
failures, honoring provider backoff hints, falling back to a second model,
mapping HTTP/TLS errors to the ``LLMError`` hierarchy, and normalizing
usage/cost fields. Prompt construction and business rules stay in the services
layer.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from pydantic import BaseModel, ValidationError
from tenacity import (
    AsyncRetrying,
    RetryCallState,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from app.core.config import Settings
from app.core.logging import get_logger
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
ClockFn = Callable[[], float]

logger = get_logger(__name__)

# Auth, payment and malformed-response errors are not transient, so they bypass
# the retry loop. Model-not-found (404) is mapped to LLMUnavailable so the
# configured fallback model gets a chance.
_RETRYABLE_EXCEPTIONS = (LLMTimeout, LLMRateLimited, LLMUnavailable)


@dataclass
class _Breaker:
    """Per-model failure counter for a simple circuit breaker."""

    failures: int = 0
    opened_at: float | None = None


class OpenRouterClient:
    """Async LLM client with retries, fallback and a circuit breaker.

    An injected ``httpx.AsyncClient`` is never closed by ``aclose()``; only a
    client created internally is owned by this object.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx.AsyncClient | None = None,
        sleep: SleepFn = asyncio.sleep,
        clock: ClockFn = time.monotonic,
    ) -> None:
        self._settings = settings
        self._sleep = sleep
        self._clock = clock
        self._attempts = settings.llm_max_retries + 1
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=f"{settings.llm_base_url.rstrip('/')}/",
            timeout=httpx.Timeout(
                connect=settings.llm_connect_timeout_seconds,
                read=settings.llm_timeout_seconds,
                write=settings.llm_connect_timeout_seconds,
                pool=settings.llm_connect_timeout_seconds,
            ),
            headers={
                "Authorization": f"Bearer {settings.llm_api_key.get_secret_value()}",
                "X-Title": "tg-bot-eng-teacher",
            },
        )
        self._default_model = settings.llm_model_fast
        backoff = max(settings.llm_retry_backoff_seconds, 0.0)
        self._default_wait = wait_exponential_jitter(
            multiplier=backoff,
            max=max(settings.llm_retry_max_backoff_seconds, backoff),
        )
        self._breakers: dict[str, _Breaker] = {}

    async def complete(
        self,
        messages: Sequence[Message],
        *,
        model: str | None = None,
        fallback_model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 800,
        response_schema: type[BaseModel] | None = None,
    ) -> LLMResponse:
        """Send a chat completion and optionally retry with a fallback model.

        When ``response_schema`` is provided the provider must return JSON that
        validates against it; the raw dict is exposed as ``LLMResponse.parsed``.
        Fallback is attempted only for transient failures, never for auth,
        payment or schema errors.
        """
        primary = model or self._default_model
        payload = self._payload(primary, messages, temperature, max_tokens, response_schema)
        try:
            return await self._complete_model(primary, payload, response_schema)
        except _RETRYABLE_EXCEPTIONS as exc:
            if not fallback_model or fallback_model == primary:
                raise
            logger.warning(
                "llm_fallback",
                primary=primary,
                fallback=fallback_model,
                error=type(exc).__name__,
            )
            fallback_payload = {**payload, "model": fallback_model}
            return await self._complete_model(fallback_model, fallback_payload, response_schema)

    async def aclose(self) -> None:
        """Close the HTTP client only if this instance created it."""
        if self._owns_client:
            await self._client.aclose()

    async def _complete_model(
        self,
        model: str,
        payload: dict[str, Any],
        response_schema: type[BaseModel] | None,
    ) -> LLMResponse:
        """Run retries for one model, updating its circuit breaker."""
        if self._is_open(model):
            raise LLMUnavailable(f"circuit breaker is open for {model}")

        try:
            response = await self._retry(payload, response_schema)
        except _RETRYABLE_EXCEPTIONS:
            self._record_failure(model)
            raise
        self._record_success(model)
        return response

    async def _retry(
        self,
        payload: dict[str, Any],
        response_schema: type[BaseModel] | None,
    ) -> LLMResponse:
        """Retry transient errors with jittered exponential backoff."""
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._attempts),
            wait=self._wait,
            retry=retry_if_exception_type(_RETRYABLE_EXCEPTIONS),
            reraise=True,
            sleep=self._sleep,
        ):
            with attempt:
                return await self._post(payload, response_schema)

        raise LLMUnavailable("LLM retry loop exhausted")

    def _wait(self, retry_state: RetryCallState) -> float:
        """Prefer the provider's Retry-After hint over exponential backoff."""
        outcome = retry_state.outcome
        error = outcome.exception() if outcome is not None else None
        if isinstance(error, LLMRateLimited) and error.retry_after is not None:
            return min(max(error.retry_after, 0.0), self._settings.llm_retry_max_backoff_seconds)
        return self._default_wait(retry_state)

    async def _post(
        self,
        payload: dict[str, Any],
        response_schema: type[BaseModel] | None,
    ) -> LLMResponse:
        """Perform one HTTP attempt and translate transport errors."""
        started = time.perf_counter()
        try:
            response = await self._client.post("chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            raise LLMTimeout("LLM request timed out") from exc
        except httpx.TransportError as exc:
            raise LLMUnavailable("LLM transport error") from exc

        if response.status_code != httpx.codes.OK:
            self._raise_for_status(response)

        latency_ms = int((time.perf_counter() - started) * 1000)
        return self._parse(response, str(payload["model"]), latency_ms, response_schema)

    def _raise_for_status(self, response: httpx.Response) -> None:
        """Map provider status codes onto the domain error hierarchy."""
        status = response.status_code
        if status in (httpx.codes.UNAUTHORIZED, httpx.codes.FORBIDDEN):
            raise LLMUnauthorized("LLM credentials are invalid")
        if status == httpx.codes.PAYMENT_REQUIRED:
            raise LLMBalanceExhausted("LLM provider balance is exhausted")
        if status == httpx.codes.TOO_MANY_REQUESTS:
            raise LLMRateLimited(
                "LLM provider rate limit reached",
                retry_after=_retry_after(response.headers.get("Retry-After")),
            )
        if status == httpx.codes.REQUEST_TIMEOUT:
            raise LLMTimeout("LLM provider timed out")
        if status == httpx.codes.NOT_FOUND:
            raise LLMUnavailable("LLM model not found")
        if status >= httpx.codes.INTERNAL_SERVER_ERROR:
            raise LLMUnavailable(f"LLM provider returned {status}")
        raise LLMError(f"LLM request failed with status {status}")

    def _parse(
        self,
        response: httpx.Response,
        model: str,
        latency_ms: int,
        response_schema: type[BaseModel] | None,
    ) -> LLMResponse:
        """Validate the envelope and extract content, usage, and cost."""
        try:
            data = response.json()
        except ValueError as exc:
            raise LLMInvalidResponse(
                "LLM response is not valid JSON", content=response.text
            ) from exc

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
        """Parse JSON content (tolerating code fences) and validate the schema."""
        text = _strip_code_fences(content)
        try:
            raw = json.loads(text)
        except json.JSONDecodeError as exc:
            raise LLMInvalidResponse(
                "structured response is not valid JSON",
                content=content,
            ) from exc
        if not isinstance(raw, dict):
            raise LLMInvalidResponse(
                "structured response must be a JSON object",
                content=content,
            )
        try:
            response_schema.model_validate(raw)
        except ValidationError as exc:
            detail = str(exc).splitlines()[0][:300]
            raise LLMInvalidResponse(
                f"structured response does not match schema: {detail}",
                content=content,
            ) from exc
        return raw

    def _payload(
        self,
        model: str,
        messages: Sequence[Message],
        temperature: float,
        max_tokens: int,
        response_schema: type[BaseModel] | None,
    ) -> dict[str, Any]:
        """Build the provider request body for one model."""
        payload: dict[str, Any] = {
            "model": model,
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
        return payload

    def _is_open(self, model: str) -> bool:
        """Return True while the model's breaker is cooling down."""
        breaker = self._breakers.get(model)
        if breaker is None or breaker.opened_at is None:
            return False
        if self._clock() - breaker.opened_at >= self._settings.llm_circuit_breaker_seconds:
            breaker.failures = 0
            breaker.opened_at = None
            return False
        return True

    def _record_failure(self, model: str) -> None:
        """Count one exhausted call and open the breaker at the threshold."""
        breaker = self._breakers.setdefault(model, _Breaker())
        breaker.failures += 1
        if breaker.failures >= self._settings.llm_circuit_breaker_failures:
            breaker.opened_at = self._clock()

    def _record_success(self, model: str) -> None:
        """Reset a model's breaker after a successful call."""
        self._breakers.pop(model, None)


def _retry_after(value: str | None) -> float | None:
    """Parse Retry-After as seconds or an HTTP date; ignore malformed values."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        moment = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return max((moment - datetime.now(UTC)).total_seconds(), 0.0)


def _strip_code_fences(content: str) -> str:
    """Remove a leading/trailing Markdown fence around a JSON object."""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    if lines:
        lines = lines[1:]
    return "\n".join(lines).strip()


def _as_int(value: Any) -> int:
    """Coerce provider counters to int, treating missing/odd values as zero."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return 0


def _as_decimal(value: Any) -> Decimal:
    """Coerce provider cost to Decimal; unknown cost is reported as zero."""
    if value is None:
        return Decimal("0")
    try:
        return Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
