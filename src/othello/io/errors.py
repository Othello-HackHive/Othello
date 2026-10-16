"""The family of errors for Part 2 (T11).

Every error our files raise on purpose inherits from ``OthelloError`` and knows:

* ``code``  a short stable word for the failure file (e.g. BAD_SETTINGS)
* ``stage`` where in the run it happened (load, settings, solve, check, write)
* ``hint``  one sentence on what to try

Imports nothing heavy, so any module can use it.
"""

from __future__ import annotations

from typing import Any

# Every failure code the failure file can carry.
FAILURE_CODES: tuple[str, ...] = (
    "BAD_INPUT",
    "INPUT_NOT_FOUND",
    "BAD_SETTINGS",
    "NO_DESIGN_FOUND",
    "DESIGN_INVALID",
    "OUTPUT_WRITE_FAILED",
    "IO_ERROR",
    "INTERNAL_ERROR",
)

STAGES: tuple[str, ...] = ("load", "settings", "solve", "check", "write", "unknown")


class OthelloError(Exception):
    """Base of every error we raise on purpose."""

    code = "INTERNAL_ERROR"
    stage = "unknown"
    hint = ""

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.details: dict[str, Any] = dict(details or {})


class NoDesignFoundError(OthelloError):
    """The solver ran but could not produce a design the checker accepts."""

    code = "NO_DESIGN_FOUND"
    stage = "solve"
    hint = "try a longer time budget, or check the plan for an impossible room (e.g. one over 100 m2)"

    def __init__(self, message: str = "no valid design was found", notes: list[str] | None = None):
        super().__init__(message, details={"notes": list(notes or [])})
        self.notes = list(notes or [])


class OutputWriteError(OthelloError):
    """An output file could not be written."""

    code = "OUTPUT_WRITE_FAILED"
    stage = "write"
    hint = "check that the output folder exists, has space and is writable"
