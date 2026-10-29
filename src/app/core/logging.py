"""Structured logging setup."""

from __future__ import annotations

import logging
import sys
from typing import TextIO

import structlog

from app.core.errors import ConfigurationError


def setup_logging(
    level: str = "INFO",
    *,
    json_logs: bool = False,
    stream: TextIO | None = None,
) -> None:
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ConfigurationError(f"Unknown log level: {level}")

    target = stream or sys.stdout
    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        stream=target,
        force=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(numeric_level),
        logger_factory=structlog.PrintLoggerFactory(file=target),
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)
