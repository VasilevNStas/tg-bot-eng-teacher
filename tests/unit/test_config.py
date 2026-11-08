from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.core.admin_config import AdminSettings
from app.core.config import BotMode, RestrictionMode, Settings


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
    assert settings.global_monthly_budget_usd == Decimal("2.00")
    assert settings.budget_alert_poll_seconds == 300
    assert settings.admin_alert_chat_id is None
    assert settings.sentry_dsn is None
    assert settings.sentry_environment == "production"
    assert settings.sentry_release is None
    assert settings.sentry_traces_sample_rate == 0.0
    assert settings.uptime_ping_url is None
    assert settings.uptime_ping_timeout_seconds == 5.0
    assert settings.metrics_snapshot_seconds == 60


def test_uptime_ping_url_accepts_http_urls() -> None:
    settings = make_settings(uptime_ping_url="https://hc-ping.com/abc")

    assert settings.uptime_ping_url is not None
    assert settings.uptime_ping_url.get_secret_value() == "https://hc-ping.com/abc"


def test_uptime_ping_url_rejects_non_http_urls() -> None:
    with pytest.raises(ValidationError, match="UPTIME_PING_URL"):
        make_settings(uptime_ping_url="hc-ping.com/abc")


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


def test_containment_and_rate_limit_defaults() -> None:
    settings = make_settings()

    assert settings.block_mode is RestrictionMode.SILENT
    assert settings.mute_mode is RestrictionMode.NEUTRAL
    assert settings.rate_limit_enabled is False
    assert settings.rate_limit_messages_per_min == 20


def make_admin_settings(**overrides: object) -> AdminSettings:
    values: dict[str, object] = {
        "admin_bot_token": "123456:admin-secret",
        "admin_ids": "100, 200",
    }
    values.update(overrides)
    return AdminSettings(_env_file=None, **values)  # type: ignore[arg-type]


def test_admin_ids_are_parsed_from_comma_separated_string() -> None:
    settings = make_admin_settings()

    assert settings.admin_id_set == frozenset({100, 200})
    assert "admin-secret" not in repr(settings)


def test_admin_ids_reject_empty_and_non_numeric_values() -> None:
    with pytest.raises(ValidationError, match="at least one"):
        make_admin_settings(admin_ids=" , ")
    with pytest.raises(ValidationError, match="non-numeric"):
        make_admin_settings(admin_ids="100,abc")


def test_admin_rate_limit_defaults() -> None:
    settings = make_admin_settings()

    assert settings.rate_limit_enabled is True
    assert settings.rate_limit_commands_per_min == 10
