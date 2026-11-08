"""Optional Sentry initialization, disabled unless SENTRY_DSN is set."""

from __future__ import annotations

import sentry_sdk
from sentry_sdk.types import Event, Hint

from app.core.config import Settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def _scrub_event(event: Event, hint: Hint) -> Event | None:
    """Drop HTTP request data so uptime/API URLs and tokens never reach Sentry."""
    event.pop("request", None)
    return event


def setup_sentry(settings: Settings, *, service: str = "bot") -> bool:
    """Initialize Sentry when configured; never send PII by default."""
    dsn = settings.sentry_dsn.get_secret_value() if settings.sentry_dsn else ""
    if not dsn.strip():
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.sentry_environment,
        release=settings.sentry_release,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        send_default_pii=False,
        max_request_body_size="never",
        before_send=_scrub_event,
    )
    sentry_sdk.set_tag("service", service)
    logger.info(
        "sentry_enabled",
        environment=settings.sentry_environment,
        release=settings.sentry_release,
        service=service,
    )
    return True


def shutdown_sentry(timeout: float = 2.0) -> None:
    """Flush buffered events before the process exits; safe when disabled."""
    sentry_sdk.flush(timeout=timeout)
