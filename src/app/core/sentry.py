"""Optional Sentry initialization, disabled unless SENTRY_DSN is set."""

from __future__ import annotations

import sentry_sdk

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def setup_sentry(settings: Settings) -> bool:
    """Initialize Sentry when configured; never send PII by default."""
    dsn = settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else ""
    if not dsn.strip():
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
    )
    logger.info("sentry_enabled", environment=settings.sentry_environment)
    return True
