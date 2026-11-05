"""Typed application settings loaded from the environment.

All values come from environment variables or ``.env``; secrets stay wrapped in
``SecretStr`` so they are not repr'ed into logs. Validation happens once at
startup, so services can assume settings are usable.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotMode(StrEnum):
    """How aiogram receives updates."""

    POLLING = "polling"
    WEBHOOK = "webhook"


class RestrictionMode(StrEnum):
    """How the user bot answers a blocked or muted user."""

    SILENT = "silent"
    NEUTRAL = "neutral"
    CONFUSED = "confused"
    EXPLICIT = "explicit"


class Settings(BaseSettings):
    """Environment-backed configuration for the bot, LLM, and storage."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    bot_token: SecretStr
    bot_mode: BotMode = BotMode.POLLING
    webhook_base_url: str | None = None
    webhook_path: str = "/webhook"
    webhook_secret: SecretStr | None = None

    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_api_key: SecretStr
    llm_model_fast: str
    llm_model_smart: str
    llm_model_fallback: str | None = None
    llm_model_roleplay: str | None = None
    llm_model_summary: str | None = None
    llm_model_judge: str | None = None
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_connect_timeout_seconds: float = Field(default=5.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    llm_retry_max_backoff_seconds: float = Field(default=8.0, ge=0)
    llm_circuit_breaker_failures: int = Field(default=5, ge=1)
    llm_circuit_breaker_seconds: float = Field(default=60.0, gt=0)
    llm_repair_attempts: int = Field(default=1, ge=0, le=3)
    llm_max_input_tokens: int = Field(default=4000, gt=0)
    llm_max_output_tokens: int = Field(default=800, gt=0)

    # Per-route generation settings. Routes are resolved into RouteSpec in
    # app.llm.models so services never hardcode a temperature or token cap.
    llm_temperature_chat: float = Field(default=0.7, ge=0, le=2)
    llm_temperature_correction: float = Field(default=0.3, ge=0, le=2)
    llm_temperature_roleplay: float = Field(default=0.8, ge=0, le=2)
    llm_temperature_summary: float = Field(default=0.2, ge=0, le=2)
    llm_temperature_judge: float = Field(default=0.0, ge=0, le=2)
    llm_max_tokens_chat: int = Field(default=600, gt=0)
    llm_max_tokens_correction: int = Field(default=800, gt=0)
    llm_max_tokens_roleplay: int = Field(default=500, gt=0)
    llm_max_tokens_summary: int = Field(default=300, gt=0)
    llm_max_tokens_judge: int = Field(default=200, gt=0)

    prompts_dir: Path = Path("./prompts")
    prompt_history_window: int = Field(default=12, ge=0, le=50)
    conversation_idle_minutes: int = Field(default=30, ge=1, le=1440)
    max_user_message_chars: int = Field(default=2000, ge=1, le=4096)
    typing_indicator_enabled: bool = True
    drill_session_size: int = Field(default=5, ge=1, le=20)
    words_card_size: int = Field(default=10, ge=1, le=20)

    db_path: Path = Path("./data/bot.db")
    daily_token_budget: int = Field(default=50_000, gt=0)
    user_lock_timeout_seconds: float = Field(default=90.0, gt=0)
    processed_updates_retention_days: int = Field(default=7, ge=1)
    history_retention_days: int = Field(default=90, ge=0, le=3650)
    reminder_poll_seconds: int = Field(default=60, ge=5, le=3600)
    reminder_batch_size: int = Field(default=100, ge=1, le=1000)

    # Containment modes. A blocked user is silent by default; a muted user gets
    # a neutral reply that does not reveal the reason or that they were caught.
    block_mode: RestrictionMode = RestrictionMode.SILENT
    mute_mode: RestrictionMode = RestrictionMode.NEUTRAL

    # Short-window flood control. Off by default so a fresh deployment behaves
    # exactly as before until the limits are tuned.
    rate_limit_enabled: bool = False
    rate_limit_messages_per_min: int = Field(default=20, ge=1, le=1000)

    log_level: str = "INFO"
    log_json: bool = False

    @model_validator(mode="after")
    def validate_webhook(self) -> Self:
        """Require webhook URL/secret in webhook mode and a sane path always."""
        if self.bot_mode is BotMode.WEBHOOK:
            if not self.webhook_base_url:
                raise ValueError("WEBHOOK_BASE_URL is required when BOT_MODE=webhook")
            if self.webhook_secret is None:
                raise ValueError("WEBHOOK_SECRET is required when BOT_MODE=webhook")
        if not self.webhook_path.startswith("/"):
            raise ValueError("WEBHOOK_PATH must start with '/'")
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings instance (cached after first read)."""
    return Settings()
