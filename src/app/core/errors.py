"""Application error hierarchy."""


class AppError(Exception):
    """Base class for expected application errors."""


class ConfigurationError(AppError):
    """Raised when configuration is missing or invalid."""
