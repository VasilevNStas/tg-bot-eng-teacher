from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import BotMode, Settings


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "bot_token": "123456:secret-token",
        "llm_api_key": "sk-test-key",
        "llm_model_fast": "vendor/fast-model",
        "llm_model_smart": "vendor/smart-model",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_defaults() -> None:
    settings = make_settings()

    assert settings.bot_mode is BotMode.POLLING
    assert settings.db_path.name == "bot.db"
    assert settings.llm_base_url == "https://openrouter.ai/api/v1"
    assert settings.daily_token_budget == 50_000


def test_webhook_mode_requires_public_url() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_BASE_URL"):
        make_settings(bot_mode=BotMode.WEBHOOK, webhook_secret="secret")


def test_webhook_mode_requires_secret() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_SECRET"):
        make_settings(
            bot_mode=BotMode.WEBHOOK,
            webhook_base_url="https://bot.example.com",
        )


def test_webhook_mode_accepts_complete_configuration() -> None:
    settings = make_settings(
        bot_mode=BotMode.WEBHOOK,
        webhook_base_url="https://bot.example.com",
        webhook_secret="secret",
    )

    assert settings.bot_mode is BotMode.WEBHOOK
    assert settings.webhook_secret is not None


def test_webhook_path_must_start_with_slash() -> None:
    with pytest.raises(ValidationError, match="WEBHOOK_PATH"):
        make_settings(webhook_path="hook")


def test_secrets_are_masked_in_repr() -> None:
    settings = make_settings()

    assert "secret-token" not in repr(settings)
    assert "sk-test-key" not in repr(settings)
