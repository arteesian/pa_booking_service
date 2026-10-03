"""structlog: JSON в проде, человекочитаемо в debug."""

from __future__ import annotations

import logging
import sys

import structlog


def configure_logging(*, level: str = "INFO", debug: bool = False) -> None:
    """Настроить structlog и stdlib-logging единым пайплайном."""
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=level)
    renderer: structlog.types.Processor = (
        structlog.dev.ConsoleRenderer() if debug else structlog.processors.JSONRenderer()
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
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelNamesMapping()[level]),
        cache_logger_on_first_use=True,
    )
