"""The run diary (T11): a log file per plan, plus optional console output.

    with run_logger("PLAN_0123", log_path) as log:
        log.info("loaded plan")
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from othello.io.failure import FailureReport
from othello.io.paths import safe_name
from othello.io.settings import non_default_settings
from othello.models.config import SolverConfig

FORMAT = "%(asctime)s %(levelname)-7s %(message)s"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


@contextmanager
def run_logger(
    plan_id: object,
    log_path: str | Path | None = None,
    console: bool = False,
    level: int = logging.INFO,
) -> Iterator[logging.Logger]:
    """A logger for one plan that writes to ``log_path`` (overwritten each run).

    Each plan gets its own logger, so two plans never write into each other's file,
    and the handlers are always removed and closed afterwards.
    """
    logger = logging.getLogger(f"othello.run.{safe_name(plan_id)}")
    _clear(logger)
    logger.setLevel(level)
    logger.propagate = False
    formatter = logging.Formatter(FORMAT, DATE_FORMAT)
    if log_path is not None:
        target = Path(log_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = logging.FileHandler(target, mode="w", encoding="utf-8")
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    if console:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    try:
        yield logger
    finally:
        _clear(logger)


def _clear(logger: logging.Logger) -> None:
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def log_settings(logger: logging.Logger, cfg: SolverConfig) -> None:
    """Write which settings differ from the defaults."""
    changed: dict[str, Any] = non_default_settings(cfg)
    logger.info("settings changed from defaults: %s", changed if changed else "none")


def log_failure(logger: logging.Logger, report: FailureReport) -> None:
    logger.error("FAILED [%s/%s] %s", report.code, report.stage, report.message)
    if report.hint:
        logger.error("hint: %s", report.hint)
