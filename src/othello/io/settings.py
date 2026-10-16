"""The settings loader (T10): build a ``SolverConfig`` from files, env and flags.

Order of priority, lowest to highest (a later layer overrides an earlier one):

    1. the built-in defaults of ``SolverConfig``
    2. a settings file       (--config FILE, or OTHELLO_CONFIG=FILE; .json or .toml)
    3. environment variables (OTHELLO_<NAME>, e.g. OTHELLO_TIME_BUDGET_S=30,
                              nested with two underscores: OTHELLO_WEIGHTS__CROSSING=6)
    4. the friendly flags    (--time-budget, --transit-policy, --weights L X O B)
    5. generic overrides     (--set weights.crossing=6, repeatable)

Typos are errors, not silently ignored: an unknown setting or an impossible
value raises ``ConfigError`` with a clear message.
"""

from __future__ import annotations

import argparse
import copy
import difflib
import json
import os
import tomllib
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from othello.io.errors import OthelloError
from othello.models.config import SolverConfig

ENV_PREFIX = "OTHELLO_"
ENV_CONFIG_FILE = "OTHELLO_CONFIG"
TRANSIT_POLICIES = ("forbid", "avoid_if_possible", "allow")
TIEBREAK_DEFINITIONS = ("max_minus_min",)


class ConfigError(OthelloError, ValueError):
    """The settings are wrong (unknown name, bad value, unreadable file)."""

    code = "BAD_SETTINGS"
    stage = "settings"
    hint = "check the setting name and value (see the message for the exact one)"


# ---------------------------------------------------------------- merging


def _defaults() -> dict[str, Any]:
    return SolverConfig().model_dump()


def _deep_update(base: dict[str, Any], changes: Mapping[str, Any], where: str = "") -> None:
    """Apply ``changes`` on top of ``base``; every key must already exist in base."""
    for key, value in changes.items():
        path = f"{where}{key}"
        if key not in base:
            hint = difflib.get_close_matches(key, list(base), n=1)
            tip = f" (did you mean '{where}{hint[0]}'?)" if hint else ""
            raise ConfigError(f"unknown setting '{path}'{tip}")
        if isinstance(base[key], dict):
            if not isinstance(value, Mapping):
                raise ConfigError(f"setting '{path}' is a group; give it as an object, not {value!r}")
            _deep_update(base[key], value, f"{path}.")
        else:
            base[key] = value


def _nested(path: str, value: Any) -> dict[str, Any]:
    """'weights.crossing' + 6  ->  {'weights': {'crossing': 6}}"""
    parts = [p for p in path.replace("__", ".").split(".") if p]
    if not parts:
        raise ConfigError(f"empty setting name in {path!r}")
    result: Any = value
    for part in reversed(parts):
        result = {part: result}
    return result


# ------------------------------------------------------------ the layers


def read_settings_file(path: str | Path) -> dict[str, Any]:
    """Read a .json or .toml settings file into a nested dictionary."""
    file = Path(path)
    if not file.is_file():
        raise ConfigError(f"settings file not found: {file}")
    try:
        if file.suffix.lower() == ".toml":
            with file.open("rb") as stream:
                data = tomllib.load(stream)
        else:
            data = json.loads(file.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, tomllib.TOMLDecodeError) as problem:
        raise ConfigError(f"cannot read settings file {file}: {problem}") from problem
    if not isinstance(data, dict):
        raise ConfigError(f"settings file {file} must contain an object at the top level")
    return data


def settings_from_env(env: Mapping[str, str]) -> dict[str, Any]:
    """Collect OTHELLO_* variables into a nested dictionary (values stay as text)."""
    found: dict[str, Any] = {}
    for name in sorted(env):
        if not name.startswith(ENV_PREFIX) or name == ENV_CONFIG_FILE:
            continue
        key = name[len(ENV_PREFIX) :].lower()
        _deep_update_raw(found, _nested(key, env[name]))
    return found


def _deep_update_raw(target: dict[str, Any], changes: Mapping[str, Any]) -> None:
    for key, value in changes.items():
        if isinstance(value, Mapping) and isinstance(target.get(key), dict):
            _deep_update_raw(target[key], value)
        else:
            target[key] = copy.deepcopy(value)


# ------------------------------------------------------------ the checks


def check_config_values(cfg: SolverConfig) -> list[str]:
    """Everything that makes these settings impossible or unsafe (empty = fine)."""
    problems: list[str] = []

    def need(ok: bool, text: str) -> None:
        if not ok:
            problems.append(text)

    need(cfg.cable_transit_policy in TRANSIT_POLICIES, f"cable_transit_policy must be one of {TRANSIT_POLICIES}")
    need(cfg.tiebreak_gap_definition in TIEBREAK_DEFINITIONS, f"tiebreak_gap_definition must be one of {TIEBREAK_DEFINITIONS}")
    need(cfg.max_nodes_per_hub >= 1, "max_nodes_per_hub must be at least 1")
    need(cfg.max_area_per_hub_m2 > 0, "max_area_per_hub_m2 must be above 0")
    need(cfg.hub_wall_clearance_m >= 0, "hub_wall_clearance_m cannot be negative")
    need(cfg.tolerance_m > 0, "tolerance_m must be above 0")
    need(cfg.cable_wall_margin_m >= 0, "cable_wall_margin_m cannot be negative")
    need(cfg.time_budget_s > 0, "time_budget_s must be above 0")
    need(0 <= cfg.final_check_reserve_fraction < 1, "final_check_reserve_fraction must be at least 0 and below 1")
    need(cfg.hub_grid_step_m > 0, "hub_grid_step_m must be above 0")
    need(cfg.grid_step_m > 0, "grid_step_m must be above 0")
    need(0 <= cfg.output_precision_decimals <= 12, "output_precision_decimals must be between 0 and 12")
    offset = cfg.parallel_lane_offset_m
    need(
        offset == 0 or offset > cfg.tolerance_m,
        "parallel_lane_offset_m must be 0 (off) or more than tolerance_m, or the lanes count as the same line",
    )
    for name in ("length", "crossing", "overlap", "bend"):
        need(getattr(cfg.weights, name) >= 0, f"weights.{name} cannot be negative")
    for name, value in cfg.limits.model_dump().items():
        need(value >= 1, f"limits.{name} must be at least 1")
    return problems


def _build(merged: dict[str, Any]) -> SolverConfig:
    try:
        cfg = SolverConfig.model_validate(merged)
    except ValidationError as problem:
        details = "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in problem.errors())
        raise ConfigError(f"bad setting value: {details}") from problem
    issues = check_config_values(cfg)
    if issues:
        raise ConfigError("invalid settings: " + "; ".join(issues))
    return cfg


# ------------------------------------------------------------- the entry


def load_config(
    file: str | Path | None = None,
    env: Mapping[str, str] | None = None,
    friendly: Mapping[str, Any] | None = None,
    overrides: Sequence[str] = (),
) -> SolverConfig:
    """Build the settings from all layers.

    ``file``      path to a .json/.toml settings file (else OTHELLO_CONFIG, else none)
    ``env``       environment variables to read (pass ``os.environ`` or a test dict;
                  ``None`` means "ignore the environment")
    ``friendly``  already-parsed friendly flags, as a nested dictionary
    ``overrides`` strings like "weights.crossing=6"
    """
    environment = env if env is not None else {}
    merged = _defaults()
    path = file or environment.get(ENV_CONFIG_FILE)
    if path:
        _deep_update(merged, read_settings_file(path))
    _deep_update(merged, settings_from_env(environment))
    if friendly:
        _deep_update(merged, friendly)
    for text in overrides:
        if "=" not in text:
            raise ConfigError(f"--set needs KEY=VALUE, got {text!r}")
        key, value = text.split("=", 1)
        _deep_update(merged, _nested(key.strip(), value.strip()))
    return _build(merged)


# --------------------------------------------------------- command line


def add_config_arguments(parser: argparse.ArgumentParser) -> None:
    """Add the settings flags to an argparse parser (used by the runner)."""
    group = parser.add_argument_group("settings")
    group.add_argument("--config", metavar="FILE", help="settings file (.json or .toml)")
    group.add_argument("--time-budget", type=float, metavar="SECONDS", help="time allowed per plan")
    group.add_argument("--transit-policy", choices=TRANSIT_POLICIES, help="may cable cross excluded rooms?")
    group.add_argument(
        "--weights",
        type=float,
        nargs=4,
        metavar=("L", "X", "O", "B"),
        help="cost weights for length, crossing, overlap, bend (default 1 5 1 0.5)",
    )
    group.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE", help="any setting, e.g. weights.crossing=6 (repeatable)")


def friendly_from_args(args: argparse.Namespace) -> dict[str, Any]:
    friendly: dict[str, Any] = {}
    if getattr(args, "time_budget", None) is not None:
        friendly["time_budget_s"] = args.time_budget
    if getattr(args, "transit_policy", None) is not None:
        friendly["cable_transit_policy"] = args.transit_policy
    weights = getattr(args, "weights", None)
    if weights is not None:
        friendly["weights"] = dict(zip(("length", "crossing", "overlap", "bend"), weights, strict=True))
    return friendly


def config_from_args(args: argparse.Namespace, env: Mapping[str, str] | None = None) -> SolverConfig:
    """Settings for a run: defaults < file < env < friendly flags < --set."""
    return load_config(
        file=getattr(args, "config", None),
        env=os.environ if env is None else env,
        friendly=friendly_from_args(args),
        overrides=getattr(args, "overrides", None) or (),
    )


# ------------------------------------------------------------- reporting


def dump_config(cfg: SolverConfig) -> dict[str, Any]:
    """The full settings as a plain nested dictionary (for saving next to the results)."""
    return cfg.model_dump()


def write_config(cfg: SolverConfig, path: str | Path) -> None:
    """Save the settings that were used, so a run can be repeated exactly."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dump_config(cfg), indent=2) + "\n", encoding="utf-8")


def non_default_settings(cfg: SolverConfig) -> dict[str, Any]:
    """Only the settings that differ from the defaults, with dotted names (for logs)."""

    def flatten(data: Mapping[str, Any], prefix: str = "") -> dict[str, Any]:
        flat: dict[str, Any] = {}
        for key, value in data.items():
            if isinstance(value, Mapping):
                flat.update(flatten(value, f"{prefix}{key}."))
            else:
                flat[f"{prefix}{key}"] = value
        return flat

    base, now = flatten(_defaults()), flatten(dump_config(cfg))
    return {key: value for key, value in now.items() if base[key] != value}
