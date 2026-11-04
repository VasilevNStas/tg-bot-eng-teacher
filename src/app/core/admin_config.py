"""Configuration for the separate admin bot.

The admin bot deliberately has its own settings class: the user-facing process
must not require ``ADMIN_BOT_TOKEN``, and the admin process does not need the
LLM keys. Both processes share only ``DB_PATH``.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AdminSettings(BaseSettings):
    """Environment-backed settings for the admin bot process."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    admin_bot_token: SecretStr
    # Kept as a string because pydantic-settings JSON-decodes list fields from
    # env before validators run; "123,456" is friendlier than a JSON array.
    admin_ids: str
    admin_alert_chat_id: int | None = None

    db_path: Path = Path("./data/bot.db")

    rate_limit_enabled: bool = True
    rate_limit_commands_per_min: int = Field(default=10, ge=1, le=1000)

    log_level: str = "INFO"
    log_json: bool = False

    @field_validator("admin_ids")
    @classmethod
    def validate_admin_ids(cls, value: str) -> str:
        """Reject empty or non-numeric allowlists before the bot starts."""
        parts = [part.strip() for part in value.split(",") if part.strip()]
        if not parts:
            raise ValueError("ADMIN_IDS must contain at least one Telegram id")
        for part in parts:
            if not part.isdigit():
                raise ValueError(f"ADMIN_IDS contains a non-numeric id: {part!r}")
        return value

    @property
    def admin_id_set(self) -> frozenset[int]:
        """Parsed numeric allowlist used by the access middleware."""
        return frozenset(int(part.strip()) for part in self.admin_ids.split(",") if part.strip())


@lru_cache(maxsize=1)
def get_admin_settings() -> AdminSettings:
    """Return the process-wide admin settings instance."""
    return AdminSettings()
