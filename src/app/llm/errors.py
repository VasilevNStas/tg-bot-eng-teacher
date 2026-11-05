"""LLM domain errors."""

from __future__ import annotations

from app.core.errors import AppError


class LLMError(AppError):
    """Base class for LLM failures."""


class LLMTimeout(LLMError):
    """Provider did not answer within the configured timeout."""


class LLMRateLimited(LLMError):
    """Provider returned 429."""

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        # Parsed from the Retry-After header when present.
        self.retry_after = retry_after


class LLMUnavailable(LLMError):
    """Provider is unreachable or returned 5xx."""


class LLMBalanceExhausted(LLMError):
    """Provider rejected the request due to insufficient balance (402)."""


class LLMUnauthorized(LLMError):
    """API key is missing, invalid or revoked (401/403)."""


class LLMInvalidResponse(LLMError):
    """Provider response is malformed or does not match the schema.

    ``content`` keeps the raw assistant text so the conversation layer can use
    it in a repair prompt or degrade to a plain reply.
    """

    def __init__(self, message: str, *, content: str | None = None) -> None:
        super().__init__(message)
        self.content = content
