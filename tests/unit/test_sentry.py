from __future__ import annotations

from unittest.mock import patch

from app.core.config import Settings
from app.core.sentry import setup_sentry


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:secret-token",
        "llm_api_key": "sk-test-key",
        "llm_model_fast": "vendor/fast-model",
        "llm_model_smart": "vendor/smart-model",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_sentry_is_disabled_without_dsn() -> None:
    settings = make_settings()

    with patch("app.core.sentry.sentry_sdk.init") as init:
        assert setup_sentry(settings) is False

    init.assert_not_called()


def test_sentry_is_disabled_for_empty_dsn() -> None:
    settings = make_settings(sentry_dsn="")

    with patch("app.core.sentry.sentry_sdk.init") as init:
        assert setup_sentry(settings) is False

    init.assert_not_called()


def test_sentry_initializes_without_pii() -> None:
    settings = make_settings(
        sentry_dsn="https://public@example.ingest.sentry.io/1",
        sentry_environment="staging",
        sentry_traces_sample_rate=0.25,
    )

    with patch("app.core.sentry.sentry_sdk.init") as init:
        assert setup_sentry(settings) is True

    init.assert_called_once_with(
        dsn="https://public@example.ingest.sentry.io/1",
        environment="staging",
        traces_sample_rate=0.25,
        send_default_pii=False,
    )
