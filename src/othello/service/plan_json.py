"""Reading an uploaded plan file: JSON in the same shape as a ResPlan record.

A ResPlan record is a Python dictionary full of Shapely shapes.  JSON cannot hold shapes, so each
shape is written as either a GeoJSON geometry object or a WKT text string, and everything else keeps
its ResPlan name:

    {"id": 14433, "area": 120.77, "wall_depth": 4.5,
     "inner":   {"type": "MultiPolygon", "coordinates": [...]},          # GeoJSON
     "wall":    "MULTIPOLYGON (((0 0, 10 0, 10 1, 0 1, 0 0)))",          # or WKT
     "window": null, "door": ..., "front_door": ...,
     "living": ..., "bedroom": ..., "kitchen": ..., "storage": ..., "bathroom": ...,
     "balcony": ..., "stair": ..., "garden": ..., "pool": ..., "parking": ..., "land": ...}

Only ``inner`` and ``area`` are required; a missing or null shape means "none of these".  Other keys
(``graph``, ``neighbor``, ``net_area``...) are ignored.  This module only reads and checks the file; the
plan itself is built by Part 1's reader.

An uploaded file is untrusted, so it is parsed with strict JSON (never pickle), with a size limit, a
limit on how many coordinates it may hold, and no NaN or infinite numbers.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from typing import Any

from shapely import wkt
from shapely.errors import ShapelyError
from shapely.geometry import GeometryCollection, MultiPolygon, Polygon, shape

from othello.io.errors import OthelloError
from othello.io.paths import safe_name
from othello.models.config import SolverConfig

MAX_BYTES = 5 * 1024 * 1024
MAX_COORDINATES = 200_000
MAX_PARTS = 5_000
MAX_ID_LENGTH = 100

REQUIRED = ("inner", "area")
SHAPE_KEYS = (
    "inner",
    "wall",
    "window",
    "door",
    "front_door",
    "living",
    "bedroom",
    "kitchen",
    "storage",
    "bathroom",
    "balcony",
    "stair",
    "garden",
    "pool",
    "parking",
    "land",
)
NUMBER_KEYS = ("area", "wall_depth")
ALLOWED_TYPES = {"Polygon", "MultiPolygon", "GeometryCollection"}


class PlanUploadError(OthelloError):
    """The uploaded plan file cannot be used (not JSON, wrong shape, too big...)."""

    code = "BAD_INPUT"
    stage = "load"
    hint = "upload a JSON file in the ResPlan layout: see docs/APP_FLOW.md (section 8) and tests/data/uploads/"


# ---------------------------------------------------------------- JSON text


def _reject_constant(name: str) -> Any:
    raise PlanUploadError(
        f"the file contains {name}, which is not a valid number in JSON"
    )


def parse_plan_json(data: bytes | str, max_bytes: int = MAX_BYTES) -> dict[str, Any]:
    """Strict JSON text -> dictionary."""
    size = len(data) if isinstance(data, bytes) else len(data.encode("utf-8"))
    if size == 0:
        raise PlanUploadError("the file is empty")
    if size > max_bytes:
        raise PlanUploadError(
            f"the file is {size} bytes; the limit is {max_bytes}",
            details={"limit_bytes": max_bytes},
        )
    try:
        text = data.decode("utf-8-sig") if isinstance(data, bytes) else data
        parsed = json.loads(text, parse_constant=_reject_constant)
    except UnicodeDecodeError as problem:
        raise PlanUploadError("the file is not UTF-8 text") from problem
    except json.JSONDecodeError as problem:
        raise PlanUploadError(
            f"the file is not valid JSON ({problem.msg} at line {problem.lineno})"
        ) from problem
    except RecursionError as problem:
        raise PlanUploadError("the JSON is nested too deeply") from problem
    if not isinstance(parsed, dict):
        raise PlanUploadError("the file must contain one JSON object at the top level")
    return parsed


# ---------------------------------------------------------------- shapes


def _count_coordinates(value: Any, budget: list[int]) -> None:
    """Count numbers inside a GeoJSON coordinates structure, refusing monsters before Shapely sees them."""
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, list):
            stack.extend(item)
        else:
            budget[0] -= 1
            if budget[0] < 0:
                raise PlanUploadError(
                    f"the plan has more than {MAX_COORDINATES} coordinate numbers"
                )


def _shape_from_json(key: str, value: Any, budget: list[int]) -> Any:
    if value is None or value == "":
        return None
    try:
        if isinstance(value, str):
            budget[0] -= value.count(",") + 1
            if budget[0] < 0:
                raise PlanUploadError(
                    f"the plan has more than {MAX_COORDINATES} coordinate numbers"
                )
            geometry = wkt.loads(value)
        elif isinstance(value, Mapping):
            if value.get("type") == "Feature":
                value = value.get("geometry")
                if value is None:
                    return None
            if not isinstance(value, Mapping) or value.get("type") not in ALLOWED_TYPES:
                raise PlanUploadError(
                    f"'{key}' must be a Polygon, MultiPolygon or GeometryCollection"
                )
            _count_coordinates(
                value.get("coordinates", value.get("geometries", [])), budget
            )
            geometry = shape(value)
        else:
            raise PlanUploadError(
                f"'{key}' must be a GeoJSON object, a WKT string or null"
            )
    except PlanUploadError:
        raise
    except (
        ShapelyError,
        ValueError,
        TypeError,
        KeyError,
        AttributeError,
        IndexError,
    ) as problem:
        raise PlanUploadError(
            f"'{key}' is not a readable shape ({type(problem).__name__}: {problem})"[
                :300
            ]
        ) from problem
    if geometry.geom_type not in ALLOWED_TYPES:
        raise PlanUploadError(f"'{key}' must hold polygons, not a {geometry.geom_type}")
    parts = len(getattr(geometry, "geoms", [geometry]))
    if parts > MAX_PARTS:
        raise PlanUploadError(f"'{key}' has {parts} parts; the limit is {MAX_PARTS}")
    if not _finite(geometry):
        raise PlanUploadError(
            f"'{key}' contains a coordinate that is not a finite number"
        )
    return geometry


def _finite(geometry: Any) -> bool:
    minx, miny, maxx, maxy = geometry.bounds if not geometry.is_empty else (0, 0, 0, 0)
    return all(math.isfinite(v) for v in (minx, miny, maxx, maxy))


# ---------------------------------------------------------------- the record


def record_from_json(data: Mapping[str, Any]) -> dict[str, Any]:
    """The uploaded dictionary -> a ResPlan-style record (shapes as Shapely objects)."""
    missing = [key for key in REQUIRED if data.get(key) in (None, "")]
    if missing:
        raise PlanUploadError(f"missing required field(s): {', '.join(missing)}")
    budget = [MAX_COORDINATES]
    record: dict[str, Any] = {}
    for key in SHAPE_KEYS:
        if key in data:
            geometry = _shape_from_json(key, data[key], budget)
            record[key] = geometry if geometry is not None else _empty_for(key)
    for key in NUMBER_KEYS:
        if key in data and data[key] is not None:
            value = data[key]
            if (
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(value)
            ):
                raise PlanUploadError(f"'{key}' must be a finite number")
            record[key] = float(value)
    if record["area"] <= 0:
        raise PlanUploadError("'area' must be above 0")
    if record["inner"].is_empty:
        raise PlanUploadError("'inner' is empty")
    record["id"] = _plan_id(data.get("id"))
    return record


def _empty_for(key: str) -> Any:
    return MultiPolygon([]) if key != "inner" else GeometryCollection()


def _plan_id(value: Any) -> str:
    if (
        value is None
        or isinstance(value, bool)
        or not isinstance(value, str | int | float)
    ):
        return "uploaded_plan"
    return safe_name(str(value), MAX_ID_LENGTH)


def plan_from_json(
    data: bytes | str | Mapping[str, Any], cfg: SolverConfig | None = None
) -> Any:
    """Uploaded bytes (or an already parsed dictionary) -> a ``FloorPlan`` built by Part 1's reader."""
    from othello.reader.load_plan import InputError, build_floor_plan_from_resplan

    parsed = parse_plan_json(data) if isinstance(data, bytes | str) else dict(data)
    record = record_from_json(parsed)
    try:
        return build_floor_plan_from_resplan(record, cfg)
    except InputError as problem:
        raise PlanUploadError(f"the plan cannot be used: {problem}") from problem


# ---------------------------------------------------------------- the other direction


def _to_geojson(geometry: Any) -> Any:
    if geometry is None or geometry.is_empty:
        return None
    from shapely.geometry import mapping

    return json.loads(json.dumps(mapping(geometry)))


def record_to_json(record: Mapping[str, Any]) -> dict[str, Any]:
    """A ResPlan record -> the upload JSON (used to make sample files from the real dataset)."""
    out: dict[str, Any] = {"id": record.get("id"), "area": float(record["area"])}
    if record.get("wall_depth") is not None:
        out["wall_depth"] = float(record["wall_depth"])
    for key in SHAPE_KEYS:
        if key in record:
            geometry = record[key]
            if (
                isinstance(geometry, Polygon | MultiPolygon | GeometryCollection)
                or geometry is None
            ):
                out[key] = _to_geojson(geometry)
    return out
