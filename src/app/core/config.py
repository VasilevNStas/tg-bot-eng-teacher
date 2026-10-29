"""Typed application settings loaded from the environment."""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from typing import Self

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class BotMode(StrEnum):
    POLLING = "polling"
    WEBHOOK = "webhook"


class Settings(BaseSettings):
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
    llm_timeout_seconds: float = Field(default=60.0, gt=0)
    llm_max_retries: int = Field(default=2, ge=0, le=10)
    llm_retry_backoff_seconds: float = Field(default=1.0, ge=0)
    llm_max_input_tokens: int = Field(default=4000, gt=0)
    llm_max_output_tokens: int = Field(default=800, gt=0)

    prompts_dir: Path = Path("./prompts")
    prompt_history_window: int = Field(default=12, ge=0, le=50)
    conversation_idle_minutes: int = Field(default=30, ge=1, le=1440)
    drill_session_size: int = Field(default=5, ge=1, le=20)

    db_path: Path = Path("./data/bot.db")
    daily_token_budget: int = Field(default=50_000, gt=0)
    user_lock_timeout_seconds: float = Field(default=90.0, gt=0)
    processed_updates_retention_days: int = Field(default=7, ge=1)
    reminder_poll_seconds: int = Field(default=60, ge=5, le=3600)
    reminder_batch_size: int = Field(default=100, ge=1, le=1000)

    log_level: str = "INFO"
    log_json: bool = False

    @model_validator(mode="after")
    def validate_webhook(self) -> Self:
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
    return Settings()
