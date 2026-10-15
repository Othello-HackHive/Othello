"""The official answer file (T09): exactly the format on slide 8.

    {"plan_id", "units": "m",
     "hubs": [{"hub_id", "room_id", "location": {"x", "y"}, "assigned_rooms",
               "assigned_nodes", "closed_sequence",
               "route_segments": [{"from", "to", "length_m", "polyline"}],
               "ring_length_m"}],
     "reported_metrics": {"hub_count", "total_length_m"}}

Rules this module enforces:
* a design is written ONLY if the checker says it is valid (checked again here,
  on the rounded numbers that will really be written);
* nothing but these fields goes into the file (debug data lives in the overlay);
* the file is written in one go, so an invalid design never leaves a half-written
  or overwritten answer behind.

Coordinates are metres.  The conversion from our internal frame to the output
frame lives in ONE place, ``to_output_coords`` (assumption P6): by default it
changes nothing (output = drawing units / scale).
"""

from __future__ import annotations

import itertools
import json
import math
import os
import tempfile
from pathlib import Path
from typing import Any

from othello.checker.validate import check_design
from othello.models.config import SolverConfig
from othello.models.design import Design, Hub, Ring, RouteLeg
from othello.models.plan import Coord, FloorPlan
from othello.models.result import ValidationResult, Violation

TOP_KEYS = ("plan_id", "units", "hubs", "reported_metrics")
HUB_KEYS = (
    "hub_id",
    "room_id",
    "location",
    "assigned_rooms",
    "assigned_nodes",
    "closed_sequence",
    "route_segments",
    "ring_length_m",
)
SEGMENT_KEYS = ("from", "to", "length_m", "polyline")
METRIC_KEYS = ("hub_count", "total_length_m")


class UnverifiedDesignError(Exception):
    """The design did not pass the checker, so no official file was written."""

    def __init__(self, violations: list[Violation]):
        self.violations = violations
        kinds = sorted({v.type for v in violations})
        super().__init__(f"design is not valid ({len(violations)} violations: {', '.join(kinds)})")


# ------------------------------------------------------------ coordinates


def _transform(plan: FloorPlan) -> tuple[float, float, bool]:
    t = plan.output_transform or {}
    return float(t.get("dx", 0.0)), float(t.get("dy", 0.0)), bool(t.get("flip_y", False))


def to_output_coords(plan: FloorPlan, point: Coord) -> Coord:
    """Internal metres -> output metres.  Default: no change (assumption P6).

    plan.output_transform may hold ``dx``, ``dy`` (added) and ``flip_y``
    (negate y before adding dy).
    """
    dx, dy, flip = _transform(plan)
    x, y = float(point[0]), float(point[1])
    return (x + dx, (-y if flip else y) + dy)


def from_output_coords(plan: FloorPlan, point: Coord) -> Coord:
    """The exact inverse of ``to_output_coords``."""
    dx, dy, flip = _transform(plan)
    x, y = float(point[0]) - dx, float(point[1]) - dy
    return (x, -y if flip else y)


# --------------------------------------------------------------- rounding


def _round(value: float, decimals: int) -> float:
    return round(float(value), decimals) + 0.0  # + 0.0 turns -0.0 into 0.0


def _round_point(point: Coord, decimals: int) -> Coord:
    return (_round(point[0], decimals), _round(point[1], decimals))


def round_design(design: Design, decimals: int) -> Design:
    """A copy of the design with every coordinate rounded the same way."""
    hubs = []
    for hub in design.hubs:
        legs = [
            RouteLeg(
                from_id=leg.from_id,
                to_id=leg.to_id,
                polyline=[_round_point(p, decimals) for p in leg.polyline],
                doors_used=list(leg.doors_used),
            )
            for leg in hub.ring.legs
        ]
        ring = Ring(hub_id=hub.ring.hub_id, sequence=list(hub.ring.sequence), legs=legs)
        hubs.append(
            Hub(
                hub_id=hub.hub_id,
                room_id=hub.room_id,
                location=_round_point(hub.location, decimals),
                assigned_rooms=list(hub.assigned_rooms),
                assigned_nodes=list(hub.assigned_nodes),
                ring=ring,
            )
        )
    return Design(plan_id=design.plan_id, hubs=hubs, solver_notes=dict(design.solver_notes))


def _length(points: list[Coord]) -> float:
    return sum(math.hypot(b[0] - a[0], b[1] - a[1]) for a, b in itertools.pairwise(points))


# ------------------------------------------------------------ build / write


def build_official(plan: FloorPlan, design: Design, cfg: SolverConfig | None = None) -> dict[str, Any]:
    """The slide 8 dictionary for a design (no checking; use write_official_json)."""
    config = cfg or SolverConfig()
    decimals = config.output_precision_decimals
    rounded = round_design(design, decimals)

    def out(point: Coord) -> list[float]:
        x, y = to_output_coords(plan, point)
        return [_round(x, decimals), _round(y, decimals)]

    hubs = []
    total = 0.0
    for hub in rounded.hubs:
        segments = []
        ring_length = 0.0
        for leg in hub.ring.legs:
            length = _round(_length(leg.polyline), decimals)
            ring_length += length
            segments.append(
                {
                    "from": leg.from_id,
                    "to": leg.to_id,
                    "length_m": length,
                    "polyline": [out(p) for p in leg.polyline],
                }
            )
        ring_length = _round(ring_length, decimals)
        total += ring_length
        x, y = out(hub.location)
        hubs.append(
            {
                "hub_id": hub.hub_id,
                "room_id": hub.room_id,
                "location": {"x": x, "y": y},
                "assigned_rooms": list(hub.assigned_rooms),
                "assigned_nodes": list(hub.assigned_nodes),
                "closed_sequence": list(hub.ring.sequence),
                "route_segments": segments,
                "ring_length_m": ring_length,
            }
        )
    return {
        "plan_id": design.plan_id,
        "units": "m",
        "hubs": hubs,
        "reported_metrics": {"hub_count": len(hubs), "total_length_m": _round(total, decimals)},
    }


def write_official_json(
    plan: FloorPlan,
    design: Design,
    path: str | Path,
    cfg: SolverConfig | None = None,
) -> ValidationResult:
    """Write the official answer file, but only for a design that passes the checker.

    The checker is run on the rounded numbers that will really be written.
    Raises ``UnverifiedDesignError`` (and writes nothing, touches nothing) otherwise.
    """
    config = cfg or SolverConfig()
    rounded = round_design(design, config.output_precision_decimals)
    result = check_design(plan, rounded, config)
    if not result.valid:
        raise UnverifiedDesignError(result.violations)

    data = build_official(plan, design, config)
    problems = official_schema_problems(data)
    if problems:  # a bug in this module, never a user error
        raise RuntimeError(f"official JSON would break the format: {problems}")

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(dir=target.parent, prefix=f".{target.name}.", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2)
            stream.write("\n")
        os.replace(temp_name, target)
    except BaseException:
        Path(temp_name).unlink(missing_ok=True)
        raise
    return result


# ----------------------------------------------------------- read it back


def design_from_official(plan: FloorPlan, data: dict[str, Any]) -> Design:
    """Rebuild a design from an official dictionary, the way the organisers' checker
    sees it: from the submitted polylines only (reported numbers are ignored)."""
    hubs = []
    for entry in data["hubs"]:
        legs = [
            RouteLeg(
                from_id=segment["from"],
                to_id=segment["to"],
                polyline=[from_output_coords(plan, (p[0], p[1])) for p in segment["polyline"]],
            )
            for segment in entry["route_segments"]
        ]
        sequence = list(entry["closed_sequence"])
        ring = Ring(hub_id=entry["hub_id"], sequence=sequence, legs=legs)
        location = from_output_coords(plan, (entry["location"]["x"], entry["location"]["y"]))
        hubs.append(
            Hub(
                hub_id=entry["hub_id"],
                room_id=entry["room_id"],
                location=location,
                assigned_rooms=list(entry["assigned_rooms"]),
                assigned_nodes=list(entry["assigned_nodes"]),
                ring=ring,
            )
        )
    return Design(plan_id=data["plan_id"], hubs=hubs)


# ------------------------------------------------------- format checking


def _is_number(value: Any) -> bool:
    return isinstance(value, int | float) and not isinstance(value, bool) and math.isfinite(value)


def official_schema_problems(data: Any) -> list[str]:
    """Everything wrong with the shape of an official dictionary (empty list = fine)."""
    problems: list[str] = []
    if not isinstance(data, dict):
        return ["top level is not an object"]
    _exact_keys(data, TOP_KEYS, "top level", problems)
    if not isinstance(data.get("plan_id"), str) or not data.get("plan_id"):
        problems.append("plan_id must be a non-empty string")
    if data.get("units") != "m":
        problems.append('units must be "m"')
    hubs = data.get("hubs")
    if not isinstance(hubs, list):
        problems.append("hubs must be a list")
        hubs = []
    for i, hub in enumerate(hubs):
        _check_hub(hub, f"hubs[{i}]", problems)
    metrics = data.get("reported_metrics")
    if not isinstance(metrics, dict):
        problems.append("reported_metrics must be an object")
    else:
        _exact_keys(metrics, METRIC_KEYS, "reported_metrics", problems)
        if not isinstance(metrics.get("hub_count"), int) or isinstance(metrics.get("hub_count"), bool):
            problems.append("reported_metrics.hub_count must be an integer")
        elif metrics["hub_count"] != len(hubs):
            problems.append("reported_metrics.hub_count does not match the number of hubs")
        if not _is_number(metrics.get("total_length_m")):
            problems.append("reported_metrics.total_length_m must be a number")
    return problems


def _exact_keys(obj: dict, keys: tuple[str, ...], where: str, problems: list[str]) -> None:
    for key in keys:
        if key not in obj:
            problems.append(f"{where}: missing {key}")
    for key in obj:
        if key not in keys:
            problems.append(f"{where}: unexpected field {key}")


def _check_hub(hub: Any, where: str, problems: list[str]) -> None:
    if not isinstance(hub, dict):
        problems.append(f"{where} is not an object")
        return
    _exact_keys(hub, HUB_KEYS, where, problems)
    for key in ("hub_id", "room_id"):
        if not isinstance(hub.get(key), str):
            problems.append(f"{where}.{key} must be a string")
    location = hub.get("location")
    if not (isinstance(location, dict) and set(location) == {"x", "y"} and all(_is_number(v) for v in location.values())):
        problems.append(f"{where}.location must be {{x, y}} numbers")
    for key in ("assigned_rooms", "assigned_nodes", "closed_sequence"):
        value = hub.get(key)
        if not (isinstance(value, list) and all(isinstance(s, str) for s in value)):
            problems.append(f"{where}.{key} must be a list of strings")
    sequence = hub.get("closed_sequence")
    segments = hub.get("route_segments")
    if isinstance(sequence, list) and sequence and (sequence[0] != sequence[-1] or sequence[0] != hub.get("hub_id")):
        problems.append(f"{where}.closed_sequence must start and end at {hub.get('hub_id')}")
    if not isinstance(segments, list):
        problems.append(f"{where}.route_segments must be a list")
        segments = []
    elif isinstance(sequence, list) and len(segments) != max(len(sequence) - 1, 0):
        problems.append(f"{where}.route_segments needs {max(len(sequence) - 1, 0)} legs, has {len(segments)}")
    for j, segment in enumerate(segments):
        _check_segment(segment, f"{where}.route_segments[{j}]", problems)
    if not _is_number(hub.get("ring_length_m")):
        problems.append(f"{where}.ring_length_m must be a number")


def _check_segment(segment: Any, where: str, problems: list[str]) -> None:
    if not isinstance(segment, dict):
        problems.append(f"{where} is not an object")
        return
    _exact_keys(segment, SEGMENT_KEYS, where, problems)
    if not isinstance(segment.get("from"), str) or not isinstance(segment.get("to"), str):
        problems.append(f"{where}.from/to must be strings")
    if not _is_number(segment.get("length_m")):
        problems.append(f"{where}.length_m must be a number")
    polyline = segment.get("polyline")
    ok = (
        isinstance(polyline, list)
        and len(polyline) >= 2
        and all(isinstance(p, list) and len(p) == 2 and all(_is_number(v) for v in p) for p in polyline)
    )
    if not ok:
        problems.append(f"{where}.polyline must be a list of at least two [x, y] pairs")
