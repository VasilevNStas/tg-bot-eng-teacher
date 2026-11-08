from __future__ import annotations

from typing import Any

from app.core.config import Settings
from app.core.sentry import _scrub_event, setup_sentry, shutdown_sentry


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:secret-token",
        "llm_api_key": "sk-test-key",
        "llm_model_fast": "vendor/fast-model",
        "llm_model_smart": "vendor/smart-model",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_setup_sentry_is_disabled_without_dsn(monkeypatch: Any) -> None:
    calls: list[dict[str, object]] = []
    monkeypatch.setattr("app.core.sentry.sentry_sdk.init", lambda **kwargs: calls.append(kwargs))

    assert setup_sentry(make_settings()) is False
    assert calls == []


def test_setup_sentry_passes_production_options(monkeypatch: Any) -> None:
    calls: list[dict[str, object]] = []
    tags: dict[str, str] = {}
    monkeypatch.setattr("app.core.sentry.sentry_sdk.init", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        "app.core.sentry.sentry_sdk.set_tag",
        lambda key, value: tags.__setitem__(key, value),
    )

    settings = make_settings(
        sentry_dsn="https://key@example.com/1",
        sentry_environment="staging",
        sentry_release="abc123",
        sentry_traces_sample_rate=0.25,
    )

    assert setup_sentry(settings, service="bot") is True
    assert calls == [
        {
            "dsn": "https://key@example.com/1",
            "environment": "staging",
            "release": "abc123",
            "traces_sample_rate": 0.25,
            "send_default_pii": False,
            "max_request_body_size": "never",
            "before_send": _scrub_event,
        }
    ]
    assert tags == {"service": "bot"}


def test_scrub_event_removes_request_data() -> None:
    event = {"message": "boom", "request": {"url": "https://hc-ping.com/secret"}}

    scrubbed = _scrub_event(event, {})

    assert scrubbed == {"message": "boom"}


def test_shutdown_sentry_flushes(monkeypatch: Any) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(
        "app.core.sentry.sentry_sdk.flush",
        lambda timeout: timeouts.append(timeout),
    )

    shutdown_sentry()

    assert timeouts == [2.0]
