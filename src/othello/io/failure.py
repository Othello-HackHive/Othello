"""The "what went wrong" note: ``<plan_id>.failure.json`` (T11).

When a plan cannot be solved, the runner writes this file INSTEAD of an official
answer.  It never contains an invented design.

    {"schema_version": 1, "status": "failed", "plan_id", "code", "stage",
     "message", "hint", "details", "settings_changed", "created_at"}
"""

from __future__ import annotations

import json
import os
import tempfile
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from othello.io.errors import OthelloError, OutputWriteError

SCHEMA_VERSION = 1
MAX_VIOLATIONS_SHOWN = 50
MAX_TRACEBACK_CHARS = 4000


class FailureReport(BaseModel):
    schema_version: int = SCHEMA_VERSION
    status: Literal["failed"] = "failed"
    plan_id: str | None = None
    code: str
    stage: str
    message: str
    hint: str = ""
    details: dict[str, Any] = Field(default_factory=dict)
    settings_changed: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def failure_from_exception(
    exc: BaseException,
    plan_id: str | None = None,
    stage: str | None = None,
    details: dict[str, Any] | None = None,
    settings_changed: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> FailureReport:
    """Turn any exception into a clear failure note (never raises)."""
    code, default_stage, hint, extra = _classify(exc)
    merged = {**extra, **(details or {})}
    return FailureReport(
        plan_id=plan_id,
        code=code,
        stage=stage or default_stage,
        message=str(exc) or type(exc).__name__,
        hint=hint,
        details=merged,
        settings_changed=dict(settings_changed or {}),
        created_at=created_at or _now(),
    )


def _classify(exc: BaseException) -> tuple[str, str, str, dict[str, Any]]:
    """(code, stage, hint, details) for an exception."""
    if isinstance(exc, OthelloError):
        extra = dict(exc.details)
        violations = getattr(exc, "violations", None)
        if violations is not None:
            extra.update(_violation_details(violations))
        return exc.code, exc.stage, exc.hint, extra

    from othello.reader.load_plan import (
        InputError,  # Part 1's error for unreadable plans
    )

    if isinstance(exc, InputError):
        return "BAD_INPUT", "load", "the plan file could not be understood; check its fields or ask the organisers", {}
    if isinstance(exc, FileNotFoundError):
        return "INPUT_NOT_FOUND", "load", "check the path to the plan file", {}
    if isinstance(exc, json.JSONDecodeError | UnicodeDecodeError):
        return "BAD_INPUT", "load", "the plan file is not valid JSON text", {}
    if isinstance(exc, OSError):
        return "IO_ERROR", "unknown", "a file or folder problem; check paths and permissions", {"os_error": type(exc).__name__}
    text = "".join(traceback.format_exception(exc))[-MAX_TRACEBACK_CHARS:]
    return (
        "INTERNAL_ERROR",
        "unknown",
        "this is a bug; please send the failure file to the team",
        {"exception": type(exc).__name__, "traceback": text},
    )


def _violation_details(violations: list[Any]) -> dict[str, Any]:
    shown = [v.model_dump(mode="json") for v in violations[:MAX_VIOLATIONS_SHOWN]]
    return {
        "violation_count": len(violations),
        "violation_codes": sorted({v.type for v in violations}),
        "violations": shown,
    }


def write_failure_json(report: FailureReport, path: str | Path) -> Path:
    """Write the failure note in one go (never leaves a half-written file)."""
    target = Path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                json.dump(report.model_dump(mode="json"), stream, indent=2)
                stream.write("\n")
            os.replace(temp_name, target)
        except BaseException:
            Path(temp_name).unlink(missing_ok=True)
            raise
    except OSError as problem:
        raise OutputWriteError(f"could not write {target}: {problem}") from problem
    return target


def read_failure_json(path: str | Path) -> FailureReport:
    return FailureReport.model_validate_json(Path(path).read_text(encoding="utf-8"))
