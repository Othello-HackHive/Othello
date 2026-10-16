"""Limits and switches of the service, read from the environment."""

from __future__ import annotations

import math
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from othello.io.settings import TRANSIT_POLICIES, ConfigError, load_config
from othello.models.config import SolverConfig
from othello.service.plan_json import MAX_BYTES

WEIGHT_NAMES = ("length", "crossing", "overlap", "bend")
MAX_WEIGHT = 1000.0
ALLOWED_SETTINGS = ("time_budget_s", "cable_transit_policy", "weights")


@dataclass(frozen=True)
class ServiceConfig:
    """Everything an operator may tune; the defaults are safe for a public demo."""

    default_budget_s: float = 60.0
    min_budget_s: float = 1.0
    max_budget_s: float = 120.0
    max_upload_bytes: int = MAX_BYTES
    max_active_jobs: int = 10
    retention_s: float = 24 * 3600.0
    stale_grace_s: float = (
        60.0  # a running job this far past its budget is declared failed
    )
    queue_timeout_s: float = 300.0  # a job still queued after this long never started
    allowed_origins: tuple[str, ...] = (
        "http://localhost:3000",
        "http://localhost:5173",
    )
    api_key: str | None = None  # when set, every /api request must send it in x-api-key

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ServiceConfig:
        env = os.environ if env is None else env
        base = cls()

        def number(name: str, default: float) -> float:
            raw = env.get(name)
            if raw in (None, ""):
                return default
            try:
                value = float(raw)
            except ValueError as problem:
                raise ConfigError(f"{name} must be a number, got {raw!r}") from problem
            if not math.isfinite(value) or value <= 0:
                raise ConfigError(f"{name} must be above 0")
            return value

        origins = env.get("OTHELLO_API_ORIGINS")
        config = cls(
            default_budget_s=number(
                "OTHELLO_API_DEFAULT_BUDGET_S", base.default_budget_s
            ),
            max_budget_s=number("OTHELLO_API_MAX_BUDGET_S", base.max_budget_s),
            max_upload_bytes=int(
                number("OTHELLO_API_MAX_UPLOAD_BYTES", base.max_upload_bytes)
            ),
            max_active_jobs=int(
                number("OTHELLO_API_MAX_ACTIVE_JOBS", base.max_active_jobs)
            ),
            retention_s=number("OTHELLO_API_RETENTION_S", base.retention_s),
            allowed_origins=tuple(o.strip() for o in origins.split(",") if o.strip())
            if origins
            else base.allowed_origins,
            api_key=env.get("OTHELLO_API_KEY") or None,
        )
        if config.default_budget_s > config.max_budget_s:
            raise ConfigError(
                "OTHELLO_API_DEFAULT_BUDGET_S cannot be above OTHELLO_API_MAX_BUDGET_S"
            )
        return config


def build_job_config(
    settings: Mapping[str, Any] | None, limits: ServiceConfig
) -> tuple[SolverConfig, dict[str, Any]]:
    """Check what the caller asked for and turn it into the solver's settings.

    Only a few settings may be chosen over the web; an unknown name or an out-of-range value is an
    error (never silently ignored).  Returns the settings object and the cleaned request.
    """
    request = dict(settings or {})
    unknown = sorted(set(request) - set(ALLOWED_SETTINGS))
    if unknown:
        raise ConfigError(
            f"setting(s) not allowed over the web: {', '.join(unknown)}; allowed: {', '.join(ALLOWED_SETTINGS)}"
        )
    friendly: dict[str, Any] = {"time_budget_s": limits.default_budget_s}
    if "time_budget_s" in request:
        value = request["time_budget_s"]
        if (
            isinstance(value, bool)
            or not isinstance(value, int | float)
            or not math.isfinite(value)
        ):
            raise ConfigError("time_budget_s must be a number")
        if not limits.min_budget_s <= value <= limits.max_budget_s:
            raise ConfigError(
                f"time_budget_s must be between {limits.min_budget_s:g} and {limits.max_budget_s:g} seconds"
            )
        friendly["time_budget_s"] = float(value)
    if "cable_transit_policy" in request:
        if request["cable_transit_policy"] not in TRANSIT_POLICIES:
            raise ConfigError(f"cable_transit_policy must be one of {TRANSIT_POLICIES}")
        friendly["cable_transit_policy"] = request["cable_transit_policy"]
    if "weights" in request:
        weights = request["weights"]
        if not isinstance(weights, Mapping) or set(weights) != set(WEIGHT_NAMES):
            raise ConfigError(f"weights must give exactly: {', '.join(WEIGHT_NAMES)}")
        for name, value in weights.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not 0 <= value <= MAX_WEIGHT
            ):
                raise ConfigError(
                    f"weights.{name} must be a number from 0 to {MAX_WEIGHT:g}"
                )
        friendly["weights"] = {name: float(weights[name]) for name in WEIGHT_NAMES}
    cleaned = {
        key: value
        for key, value in friendly.items()
        if key in request or key == "time_budget_s"
    }
    return load_config(friendly=friendly), cleaned
