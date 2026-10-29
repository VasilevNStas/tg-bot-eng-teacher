from __future__ import annotations

import pytest

from app.core.errors import ConfigurationError
from app.core.logging import setup_logging


def test_unknown_log_level_is_rejected() -> None:
    with pytest.raises(ConfigurationError, match="Unknown log level"):
        setup_logging("verbose")


def test_logging_setup_accepts_known_levels() -> None:
    setup_logging("DEBUG", json_logs=True)
    setup_logging("INFO", json_logs=False)
