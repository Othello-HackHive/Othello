"""The picture data (T14): ``<plan_id>.overlay.json``, read by Ritvik's frontend.

Never submitted to the organisers.  It holds everything needed to DRAW a plan:
the house (walls, windows, rooms, doors), the hubs, nodes, visiting order,
cables, red violations, crossing/overlap/bend markers, the score and the
pass/fail line of every rule.

It can describe a valid design, an invalid one (violations shown in red), or no
design at all (just the house plus the reason).  ``status`` says which:

    "ok"               a design that passed every rule
    "invalid"          a design with violations (drawn so people can see them)
    "no_design_found"  nothing to draw but the house

``schema_version`` is 1.  Coordinates are metres in the SAME frame as the
official answer file (same ``to_output_coords``), rounded the same way.
"""

from __future__ import annotations

import itertools
import json
import math
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from othello.checker.metrics import design_markers
from othello.io.atomic import atomic_write_text
from othello.io.official import to_output_coords
from othello.io.settings import non_default_settings
from othello.models.config import SolverConfig
from othello.models.design import Design
from othello.models.plan import Coord, FloorPlan
from othello.models.result import HubCountStatus, ValidationResult
from othello.territory import hub_lower_bound

SCHEMA_VERSION = 1
STATUSES = ("ok", "invalid", "no_design_found")

OVERLAY_KEYS = (
    "schema_version",
    "plan_id",
    "status",
    "valid",
    "units",
    "scale",
    "bounds",
    "geometry",
    "nodes",
    "hubs",
    "violations",
    "markers",
    "metrics",
    "checks",
    "bounds_report",
    "runtime_s",
    "notes",
    "settings_changed",
)
GEOMETRY_KEYS = ("inner", "walls", "windows", "rooms", "openings")
MARKER_KEYS = ("crossings", "overlaps", "bends")
HUB_KEYS = (
    "hub_id",
    "room_id",
    "point",
    "color_index",
    "rooms",
    "nodes",
    "sequence",
    "ring_length_m",
    "area_m2",
    "legs",
)
LEG_KEYS = ("from", "to", "order", "length_m", "polyline", "doors_used")
NODE_KEYS = ("node_id", "room_id", "point", "hub_id", "order")
ROOM_KEYS = ("room_id", "kind", "eligible", "inside_inner", "area_m2", "hub_id", "polygon", "polygons")
OPENING_KEYS = ("opening_id", "kind", "rooms", "gap", "counts_for_zone")
VIOLATION_KEYS = ("type", "hub_id", "ring_id", "segment_index", "point", "detail")
CHECK_KEYS = ("rule_id", "label", "passed", "value", "detail")
BOUNDS_REPORT_KEYS = ("lower_bound_hubs", "proven_minimum", "tried")


# ------------------------------------------------------------- helpers


def _pointer(plan: FloorPlan, decimals: int):
    def out(point: Coord) -> list[float]:
        x, y = to_output_coords(plan, (float(point[0]), float(point[1])))
        return [round(x, decimals) + 0.0, round(y, decimals) + 0.0]

    return out


def _polygons(geometry: Any, out) -> list[list[list[list[float]]]]:
    """Shapely shape -> list of polygons; a polygon is a list of rings (outer first),
    a ring is a list of [x, y]."""
    if geometry is None or geometry.is_empty:
        return []
    kind = geometry.geom_type
    if kind == "Polygon":
        rings = [geometry.exterior, *geometry.interiors]
        return [[[out(p) for p in ring.coords] for ring in rings]]
    if hasattr(geometry, "geoms"):
        pieces: list[list[list[list[float]]]] = []
        for part in geometry.geoms:
            pieces.extend(_polygons(part, out))
        return pieces
    return []


def _largest_outline(polygons: list[list[list[list[float]]]]) -> list[list[float]]:
    if not polygons:
        return []

    def area(polygon: list[list[list[float]]]) -> float:
        ring = polygon[0]
        return abs(sum(a[0] * b[1] - b[0] * a[1] for a, b in itertools.pairwise(ring))) / 2

    return max(polygons, key=area)[0]


def _bounds(plan: FloorPlan, out) -> dict[str, list[float]]:
    min_x, min_y, max_x, max_y = plan.inner_m.bounds
    corners = [out((x, y)) for x in (min_x, max_x) for y in (min_y, max_y)]
    xs, ys = [c[0] for c in corners], [c[1] for c in corners]
    return {"min": [min(xs), min(ys)], "max": [max(xs), max(ys)]}


def _leg_length(polyline: list[list[float]]) -> float:
    return sum(math.dist(a, b) for a, b in itertools.pairwise(polyline))


# ----------------------------------------------------------------- build


def build_overlay(
    plan: FloorPlan,
    design: Design | None = None,
    validation: ValidationResult | None = None,
    *,
    hub_count_status: HubCountStatus | None = None,
    runtime_s: float | None = None,
    notes: Sequence[str] = (),
    cfg: SolverConfig | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    """The overlay dictionary for a plan (and optionally a design and its report card)."""
    config = cfg or SolverConfig()
    decimals = config.output_precision_decimals
    out = _pointer(plan, decimals)

    hubs_of_room: dict[str, str] = {}
    hubs_of_node: dict[str, str] = {}
    if design is not None:
        for hub in design.hubs:
            for room_id in hub.assigned_rooms:
                hubs_of_room.setdefault(room_id, hub.hub_id)
            for node_id in hub.assigned_nodes:
                hubs_of_node.setdefault(node_id, hub.hub_id)

    if status is None:
        status = "no_design_found" if design is None else ("ok" if validation and validation.valid else "invalid")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")

    return {
        "schema_version": SCHEMA_VERSION,
        "plan_id": plan.plan_id,
        "status": status,
        "valid": bool(design is not None and validation is not None and validation.valid),
        "units": "m",
        "scale": plan.scale,
        "bounds": _bounds(plan, out),
        "geometry": _geometry(plan, out, hubs_of_room),
        "nodes": _nodes(plan, design, out, hubs_of_node),
        "hubs": _hubs(plan, design, out, decimals),
        "violations": _violations(validation, out),
        "markers": _markers(design, config, out),
        "metrics": _metrics(validation, decimals),
        "checks": [check.model_dump() for check in validation.checks] if validation else [],
        "bounds_report": _bounds_report(plan, hub_count_status, config),
        "runtime_s": None if runtime_s is None else round(float(runtime_s), 3),
        "notes": [str(n) for n in notes],
        "settings_changed": non_default_settings(config),
    }


def _geometry(plan: FloorPlan, out, hubs_of_room: dict[str, str]) -> dict[str, Any]:
    rooms = []
    for room in plan.rooms.values():
        polygons = _polygons(room.polygon_m, out)
        rooms.append(
            {
                "room_id": room.room_id,
                "kind": room.kind,
                "eligible": room.eligible,
                "inside_inner": room.inside_inner,
                "area_m2": round(float(room.area_m2), 4),
                "hub_id": hubs_of_room.get(room.room_id),
                "polygon": _largest_outline(polygons),
                "polygons": polygons,
            }
        )
    openings = [
        {
            "opening_id": o.opening_id,
            "kind": o.kind,
            "rooms": list(o.rooms),
            "gap": [out(p) for p in o.gap_polyline_m],
            "counts_for_zone": o.counts_for_zone,
        }
        for o in plan.openings.values()
    ]
    return {
        "inner": _polygons(plan.inner_m, out),
        "walls": _polygons(plan.walls_m, out),
        "windows": _polygons(plan.windows_m, out),
        "rooms": rooms,
        "openings": openings,
    }


def _nodes(plan: FloorPlan, design: Design | None, out, hubs_of_node: dict[str, str]) -> list[dict[str, Any]]:
    order_of: dict[str, int] = {}
    if design is not None:
        for hub in design.hubs:
            stops = [s for s in hub.ring.sequence[1:-1] if s in plan.nodes]
            for position, node_id in enumerate(stops, start=1):
                order_of.setdefault(node_id, position)
    return [
        {
            "node_id": node.node_id,
            "room_id": node.room_id,
            "point": out(node.point_m),
            "hub_id": hubs_of_node.get(node.node_id),
            "order": order_of.get(node.node_id),
        }
        for node in plan.nodes.values()
    ]


def _hubs(plan: FloorPlan, design: Design | None, out, decimals: int) -> list[dict[str, Any]]:
    if design is None:
        return []
    hubs = []
    for index, hub in enumerate(design.hubs):
        legs = []
        for order, leg in enumerate(hub.ring.legs, start=1):
            polyline = [out(p) for p in leg.polyline]
            legs.append(
                {
                    "from": leg.from_id,
                    "to": leg.to_id,
                    "order": order,
                    "length_m": round(_leg_length(polyline), decimals),
                    "polyline": polyline,
                    "doors_used": list(leg.doors_used),
                }
            )
        hubs.append(
            {
                "hub_id": hub.hub_id,
                "room_id": hub.room_id,
                "point": out(hub.location),
                "color_index": index,
                "rooms": list(hub.assigned_rooms),
                "nodes": list(hub.assigned_nodes),
                "sequence": list(hub.ring.sequence),
                "ring_length_m": round(sum(leg["length_m"] for leg in legs), decimals),
                "area_m2": round(sum(plan.rooms[r].area_m2 for r in hub.assigned_rooms if r in plan.rooms), 4),
                "legs": legs,
            }
        )
    return hubs


def _violations(validation: ValidationResult | None, out) -> list[dict[str, Any]]:
    if validation is None:
        return []
    return [
        {
            "type": v.type,
            "hub_id": v.hub_id,
            "ring_id": v.ring_id,
            "segment_index": v.segment_index,
            "point": out(v.point) if v.point is not None else None,
            "detail": v.detail,
        }
        for v in validation.violations
    ]


def _markers(design: Design | None, config: SolverConfig, out) -> dict[str, list]:
    if design is None:
        return {"crossings": [], "overlaps": [], "bends": []}
    found = design_markers(design, config)
    return {
        "crossings": [out(p) for p in found["crossings"]],
        "overlaps": [[out(a), out(b)] for a, b in found["overlaps"]],
        "bends": [out(p) for p in found["bends"]],
    }


def _metrics(validation: ValidationResult | None, decimals: int) -> dict[str, Any] | None:
    if validation is None or validation.metrics is None:
        return None
    m = validation.metrics
    return {
        "hub_count": m.hub_count,
        "L": round(m.L, decimals),
        "X": m.X,
        "O": round(m.O, decimals),
        "B": m.B,
        "J": round(m.J, decimals),
        "area_gap_m2": round(m.area_gap_m2, decimals),
        "per_hub": {
            hub_id: {key: (round(value, decimals) if isinstance(value, float) else value) for key, value in info.items()}
            for hub_id, info in m.per_hub.items()
        },
    }


def _bounds_report(plan: FloorPlan, status: HubCountStatus | None, config: SolverConfig) -> dict[str, Any]:
    if status is None:
        return {"lower_bound_hubs": hub_lower_bound(plan, config), "proven_minimum": None, "tried": {}}
    return {
        "lower_bound_hubs": status.lower_bound,
        "proven_minimum": status.proven_minimum,
        "tried": {str(count): label for count, label in sorted(status.tried.items())},
    }


# ----------------------------------------------------------------- write


def write_overlay_json(
    plan: FloorPlan,
    design: Design | None,
    validation: ValidationResult | None,
    path: str | Path,
    **options: Any,
) -> Path:
    """Write the overlay file safely (valid, invalid or no design; all are drawable)."""
    data = build_overlay(plan, design, validation, **options)
    problems = overlay_schema_problems(data)
    if problems:  # a bug in this module, never a user error
        raise RuntimeError(f"overlay JSON would break its own format: {problems}")
    return atomic_write_text(path, json.dumps(data, indent=2) + "\n")


# --------------------------------------------------------- format check


def _keys(obj: Any, keys: tuple[str, ...], where: str, problems: list[str]) -> bool:
    if not isinstance(obj, dict):
        problems.append(f"{where} is not an object")
        return False
    for key in keys:
        if key not in obj:
            problems.append(f"{where}: missing {key}")
    for key in obj:
        if key not in keys:
            problems.append(f"{where}: unexpected field {key}")
    return True


def _is_point(value: Any) -> bool:
    return (
        isinstance(value, list)
        and len(value) == 2
        and all(isinstance(v, int | float) and not isinstance(v, bool) and math.isfinite(v) for v in value)
    )


def overlay_schema_problems(data: Any) -> list[str]:
    """Everything wrong with the shape of an overlay dictionary (empty = fine)."""
    problems: list[str] = []
    if not _keys(data, OVERLAY_KEYS, "overlay", problems):
        return problems
    if data.get("schema_version") != SCHEMA_VERSION:
        problems.append(f"schema_version must be {SCHEMA_VERSION}")
    if data.get("status") not in STATUSES:
        problems.append(f"status must be one of {STATUSES}")
    if data.get("units") != "m":
        problems.append('units must be "m"')
    if not isinstance(data.get("valid"), bool):
        problems.append("valid must be true or false")
    bounds = data.get("bounds")
    if not (_keys(bounds, ("min", "max"), "bounds", problems) and _is_point(bounds["min"]) and _is_point(bounds["max"])):
        problems.append("bounds.min and bounds.max must be [x, y]")
    if _keys(data.get("geometry"), GEOMETRY_KEYS, "geometry", problems):
        geometry = data["geometry"]
        for room in geometry["rooms"]:
            _keys(room, ROOM_KEYS, "geometry.rooms[]", problems)
        for opening in geometry["openings"]:
            _keys(opening, OPENING_KEYS, "geometry.openings[]", problems)
    for node in data.get("nodes", []):
        if _keys(node, NODE_KEYS, "nodes[]", problems) and not _is_point(node["point"]):
            problems.append("nodes[].point must be [x, y]")
    for hub in data.get("hubs", []):
        if _keys(hub, HUB_KEYS, "hubs[]", problems):
            if not _is_point(hub["point"]):
                problems.append("hubs[].point must be [x, y]")
            for leg in hub["legs"]:
                if _keys(leg, LEG_KEYS, "hubs[].legs[]", problems) and not all(_is_point(p) for p in leg["polyline"]):
                    problems.append("hubs[].legs[].polyline must be a list of [x, y]")
    for violation in data.get("violations", []):
        _keys(violation, VIOLATION_KEYS, "violations[]", problems)
    _keys(data.get("markers"), MARKER_KEYS, "markers", problems)
    for check in data.get("checks", []):
        _keys(check, CHECK_KEYS, "checks[]", problems)
    _keys(data.get("bounds_report"), BOUNDS_REPORT_KEYS, "bounds_report", problems)
    if not isinstance(data.get("notes"), list):
        problems.append("notes must be a list")
    if not isinstance(data.get("settings_changed"), dict):
        problems.append("settings_changed must be an object")
    return problems
