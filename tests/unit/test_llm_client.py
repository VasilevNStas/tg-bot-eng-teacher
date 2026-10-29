from __future__ import annotations

import json

import httpx
import pytest
from pydantic import BaseModel

from app.core.config import Settings
from app.llm.errors import (
    LLMBalanceExhausted,
    LLMInvalidResponse,
    LLMTimeout,
    LLMUnauthorized,
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
) -> tuple[OpenRouterClient, httpx.AsyncClient]:
    settings = make_settings(llm_max_retries=max_retries)
    http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1/",
    )
    return OpenRouterClient(settings, client=http_client), http_client


def success_payload(content: str = "Hello") -> dict[str, object]:
    return {
        "model": "vendor/fast-model",
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
