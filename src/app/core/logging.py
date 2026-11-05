"""Structured logging setup.

Console output is for humans, JSON is for log collectors. ``setup_logging`` is
idempotent thanks to ``force=True`` and is called once at process start; all
modules then obtain loggers through ``get_logger``.
"""

from __future__ import annotations

import io
import logging
import sys
from typing import TextIO, cast

import structlog

from app.core.errors import ConfigurationError


class _StdStreamProxy(io.TextIOBase):
    """Resolve ``sys.stdout``/``sys.stderr`` at write time.

    Pytest replaces these streams per test and closes the old capture object.
    Binding a logger factory to the object seen at setup time would leave it
    writing to a closed file after teardown.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    def writable(self) -> bool:
        return True

    def write(self, text: str) -> int:
        return getattr(sys, self._name).write(text)

    def flush(self) -> None:
        getattr(sys, self._name).flush()


def _proxy(name: str) -> TextIO:
    """Return a TextIO-compatible dynamic stream proxy."""
    return cast(TextIO, _StdStreamProxy(name))


def setup_logging(
    level: str = "INFO",
    *,
    json_logs: bool = False,
    stream: TextIO | None = None,
) -> None:
    """Configure stdlib logging and structlog with a shared level and stream."""
    numeric_level = getattr(logging, level.upper(), None)
    if not isinstance(numeric_level, int):
        raise ConfigurationError(f"Unknown log level: {level}")

    target = stream or sys.stdout
    # force=True makes repeated setup safe (tests, CLI, bot in one process).
    logging.basicConfig(
        level=numeric_level,
        format="%(message)s",
        stream=target,
        force=True,
    )

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer() if json_logs else structlog.dev.ConsoleRenderer()
    )

    # With an explicit stream (CLI/tests) bind it directly. Without one, let
    # PrintLogger resolve sys.stdout at call time so pytest capture teardown
    # cannot leave a closed file bound to the logger factory.
    if stream is None or stream is sys.stdout:
        logger_factory = structlog.PrintLoggerFactory(file=_proxy("stdout"))
    elif stream is sys.stderr:
        logger_factory = structlog.PrintLoggerFactory(file=_proxy("stderr"))
    else:
        logger_factory = structlog.PrintLoggerFactory(file=stream)

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
        logger_factory=logger_factory,
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Return a structlog logger bound to the module name."""
    return structlog.get_logger(name)
