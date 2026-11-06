from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.core.metrics import MetricsRegistry
from app.llm.errors import (
    LLMBalanceExhausted,
    LLMInvalidResponse,
    LLMTimeout,
    LLMUnauthorized,
    LLMUnavailable,
)
from app.llm.openrouter import OpenRouterClient
from app.llm.port import Message


class Answer(BaseModel):
    answer: str


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:secret-token",
        "llm_api_key": "sk-test-key",
        "llm_model_fast": "vendor/fast-model",
        "llm_model_smart": "vendor/smart-model",
        "llm_retry_backoff_seconds": 0.0,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def make_client(
    handler: httpx.MockTransportHandler,
    *,
    max_retries: int = 0,
    settings: Settings | None = None,
    sleep: Any = None,
    clock: Any = None,
    metrics: MetricsRegistry | None = None,
) -> tuple[OpenRouterClient, httpx.AsyncClient]:
    settings = settings or make_settings(llm_max_retries=max_retries)
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1/",
    )
    kwargs: dict[str, Any] = {"client": http_client}
    if sleep is not None:
        kwargs["sleep"] = sleep
    if clock is not None:
        kwargs["clock"] = clock
    if metrics is not None:
        kwargs["metrics"] = metrics
    return OpenRouterClient(settings, **kwargs), http_client


def success_payload(
    content: str = "Hello", *, model: str = "vendor/fast-model"
) -> dict[str, object]:
    return {
        "model": model,
        "choices": [{"message": {"content": content}}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "cost": 0.0001},
    }


async def test_complete_parses_text_response() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["model"] == "vendor/fast-model"
        assert body["messages"] == [{"role": "user", "content": "Hi"}]
        return httpx.Response(200, json=success_payload())

    client, http_client = make_client(handler)

    response = await client.complete([Message(role="user", content="Hi")])

    assert response.content == "Hello"
    assert response.model == "vendor/fast-model"
    assert response.input_tokens == 10
    assert response.output_tokens == 5
    assert response.total_tokens == 15
    assert str(response.cost_usd) == "0.0001"
    assert response.parsed is None

    await http_client.aclose()


async def test_retries_on_rate_limit() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, json={"error": "rate limited"})
        return httpx.Response(200, json=success_payload())

    client, http_client = make_client(handler, max_retries=1)

    response = await client.complete([Message(role="user", content="Hi")])

    assert response.content == "Hello"
    assert calls == 2

    await http_client.aclose()


async def test_does_not_retry_on_insufficient_balance() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(402, json={"error": "insufficient credits"})

    client, http_client = make_client(handler, max_retries=2)

    with pytest.raises(LLMBalanceExhausted):
        await client.complete([Message(role="user", content="Hi")])

    assert calls == 1

    await http_client.aclose()


async def test_unauthorized_is_not_retried() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid key"})

    client, http_client = make_client(handler, max_retries=2)

    with pytest.raises(LLMUnauthorized):
        await client.complete([Message(role="user", content="Hi")])

    await http_client.aclose()


async def test_structured_response_is_parsed_and_validated() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["response_format"]["type"] == "json_schema"
        assert body["response_format"]["json_schema"]["name"] == "Answer"
        return httpx.Response(200, json=success_payload(json.dumps({"answer": "ok"})))

    client, http_client = make_client(handler)

    response = await client.complete(
        [Message(role="user", content="Hi")],
        response_schema=Answer,
    )

    assert response.parsed == {"answer": "ok"}

    await http_client.aclose()


async def test_invalid_structured_response_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success_payload(json.dumps({"wrong": "shape"})))

    client, http_client = make_client(handler)

    with pytest.raises(LLMInvalidResponse):
        await client.complete([Message(role="user", content="Hi")], response_schema=Answer)

    await http_client.aclose()


async def test_timeout_is_mapped_and_retried() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise httpx.ReadTimeout("timed out", request=request)

    client, http_client = make_client(handler, max_retries=1)

    with pytest.raises(LLMTimeout):
        await client.complete([Message(role="user", content="Hi")])

    assert calls == 2

    await http_client.aclose()


async def test_fallback_model_is_used_for_transient_failure() -> None:
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        model = json.loads(request.content)["model"]
        models.append(model)
        if model == "primary/model":
            return httpx.Response(500, json={"error": "down"})
        return httpx.Response(200, json=success_payload("Fallback", model=model))

    client, http_client = make_client(handler)

    response = await client.complete(
        [Message(role="user", content="Hi")],
        model="primary/model",
        fallback_model="fallback/model",
    )

    assert response.content == "Fallback"
    assert models == ["primary/model", "fallback/model"]
    await http_client.aclose()


async def test_fallback_is_not_used_for_payment_error() -> None:
    models: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return httpx.Response(402, json={"error": "no credit"})

    client, http_client = make_client(handler)

    with pytest.raises(LLMBalanceExhausted):
        await client.complete(
            [Message(role="user", content="Hi")],
            model="primary/model",
            fallback_model="fallback/model",
        )

    assert models == ["primary/model"]
    await http_client.aclose()


async def test_retry_after_is_honored() -> None:
    calls = 0
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "7"}, json={})
        return httpx.Response(200, json=success_payload())

    settings = make_settings(
        llm_max_retries=1,
        llm_retry_max_backoff_seconds=60.0,
    )
    client, http_client = make_client(handler, settings=settings, sleep=fake_sleep)

    await client.complete([Message(role="user", content="Hi")])

    assert sleeps == [7.0]
    await http_client.aclose()


async def test_circuit_breaker_opens_and_recovers() -> None:
    calls = 0
    now = [100.0]
    failing = True

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if failing:
            return httpx.Response(500, json={"error": "down"})
        return httpx.Response(200, json=success_payload())

    settings = make_settings(
        llm_max_retries=0,
        llm_circuit_breaker_failures=2,
        llm_circuit_breaker_seconds=60.0,
    )
    client, http_client = make_client(handler, settings=settings, clock=lambda: now[0])

    for _ in range(2):
        with pytest.raises(LLMUnavailable):
            await client.complete([Message(role="user", content="Hi")])

    assert calls == 2

    # The breaker is open: no HTTP call is made.
    with pytest.raises(LLMUnavailable):
        await client.complete([Message(role="user", content="Hi")])
    assert calls == 2

    failing = False
    now[0] += 61
    response = await client.complete([Message(role="user", content="Hi")])

    assert response.content == "Hello"
    assert calls == 3
    await http_client.aclose()


async def test_structured_response_tolerates_code_fences() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        content = f"```json\n{json.dumps({'answer': 'ok'})}\n```"
        return httpx.Response(200, json=success_payload(content))

    client, http_client = make_client(handler)

    response = await client.complete(
        [Message(role="user", content="Hi")],
        response_schema=Answer,
    )

    assert response.parsed == {"answer": "ok"}
    await http_client.aclose()


async def test_metrics_record_success_and_error() -> None:
    metrics = MetricsRegistry()

    def success(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=success_payload())

    client, http_client = make_client(success, metrics=metrics)
    await client.complete([Message(role="user", content="Hi")])
    await http_client.aclose()

    counters = metrics.snapshot()["counters"]
    assert counters["llm_calls_total"] == 1.0
    assert counters["llm_input_tokens_total"] == 10.0
    assert counters["llm_output_tokens_total"] == 5.0
    assert counters["llm_cost_usd_total"] == 0.0001

    def failure(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, json={"error": "down"})

    failing, failing_http = make_client(failure, metrics=metrics)
    with pytest.raises(LLMUnavailable):
        await failing.complete([Message(role="user", content="Hi")])
    await failing_http.aclose()

    counters = metrics.snapshot()["counters"]
    assert counters["llm_errors_total"] == 1.0
    timers = metrics.snapshot()["timers"]
    assert timers["llm_duration_ms"]["count"] == 2
