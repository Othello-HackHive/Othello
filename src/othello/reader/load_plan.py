"""ResPlan adapter and normalisation boundary for Part 1.

Only this module knows the source record format.  The solver receives metres,
stable IDs, and explicitly labelled room links.
"""

from __future__ import annotations

import math
import pickle
from pathlib import Path
from typing import Any, Iterable

from shapely import affinity, make_valid
from shapely.geometry import GeometryCollection, Point
from shapely.ops import unary_union

from othello.models import FloorPlan, Node, Opening, Room, SolverConfig

ELIGIBLE_KINDS = ("living", "bedroom", "kitchen", "storage")
EXCLUDED_KINDS = ("bathroom", "balcony", "stair", "garden", "pool", "parking", "land")
ZONE_EDGE_TYPES = {"via_door", "via_opening"}


class InputError(ValueError):
    pass


def _parts(geometry: Any) -> list[Any]:
    if geometry is None or geometry.is_empty:
        return []
    if hasattr(geometry, "geoms") and geometry.geom_type.startswith("Multi"):
        return [part for part in geometry.geoms if not part.is_empty]
    return [geometry]


def _repair(geometry: Any, label: str, repairs: list[str], config: SolverConfig) -> Any:
    if geometry is None:
        return geometry
    if geometry.is_valid:
        return geometry
    if not config.repair_geometry:
        raise InputError(f"Invalid geometry for {label}")
    repaired = make_valid(geometry)
    repairs.append(f"Repaired invalid geometry: {label}")
    return repaired


def _scale_geometry(geometry: Any, scale: float) -> Any:
    return affinity.scale(geometry, xfact=1 / scale, yfact=1 / scale, origin=(0, 0))


def _graph_node_id(kind: str, index: int) -> str:
    return f"{kind}_{index}"


def _safe_point(room_m: Any, walls_m: Any, clearance_m: float) -> Point:
    allowed = room_m.difference(walls_m.buffer(clearance_m))
    if allowed.is_empty:
        raise InputError("Eligible room has no point with required wall clearance")
    return allowed.representative_point()


def _gap_polyline(geometry: Any) -> list[tuple[float, float]]:
    boundary = geometry.boundary
    if hasattr(boundary, "geoms"):
        boundary = max(boundary.geoms, key=lambda item: item.length)
    coordinates = list(boundary.coords)
    if len(coordinates) >= 2:
        return [(float(coordinates[0][0]), float(coordinates[0][1])), (float(coordinates[1][0]), float(coordinates[1][1]))]
    centroid = geometry.representative_point()
    return [(float(centroid.x), float(centroid.y))] * 2


def build_floor_plan_from_resplan(record: dict[str, Any], config: SolverConfig | None = None) -> FloorPlan:
    """Normalise one raw ResPlan record.

    Raw ResPlan does not provide Cable Ring node points.  This development
    adapter generates deterministic temporary points; an organiser adapter can
    replace only that step when fixed points arrive.
    """

    config = config or SolverConfig()
    repairs: list[str] = []
    if not record.get("inner") or record["inner"].is_empty:
        raise InputError("Plan is missing a non-empty inner boundary")
    if not record.get("area") or float(record["area"]) <= 0:
        raise InputError("Plan has no positive area")

    inner_units = _repair(record["inner"], "inner", repairs, config)
    scale = math.sqrt(inner_units.area / float(record["area"]))
    if scale <= 0:
        raise InputError("Computed scale is not positive")

    geometries_units: dict[str, Any] = {}
    for kind in (*ELIGIBLE_KINDS, *EXCLUDED_KINDS, "wall", "window", "door", "front_door"):
        geometry = record.get(kind)
        if geometry is not None:
            geometries_units[kind] = _repair(geometry, kind, repairs, config)
    inner_m = _scale_geometry(inner_units, scale)
    walls_m = _scale_geometry(geometries_units.get("wall", GeometryCollection()), scale)
    windows_m = _scale_geometry(geometries_units.get("window", GeometryCollection()), scale)
    free_space_m = inner_m.difference(unary_union([walls_m, windows_m]))

    rooms: dict[str, Room] = {}
    component_to_room: dict[str, str] = {}
    room_index = 1
    for kind in (*ELIGIBLE_KINDS, *EXCLUDED_KINDS):
        for index, polygon_units in enumerate(_parts(geometries_units.get(kind))):
            if polygon_units.geom_type != "Polygon":
                continue
            room_id = f"R{room_index:02d}"
            room_index += 1
            polygon_m = _scale_geometry(polygon_units, scale)
            rooms[room_id] = Room(
                room_id=room_id,
                kind=kind,
                eligible=kind in ELIGIBLE_KINDS,
                polygon_m=polygon_m,
                polygon_units=polygon_units,
                area_m2=float(polygon_m.area),
                inside_inner=inner_m.covers(polygon_m.representative_point()),
            )
            component_to_room[_graph_node_id(kind, index)] = room_id

    nodes: dict[str, Node] = {}
    hub_zones_m: dict[str, Any] = {}
    for room in rooms.values():
        if not room.eligible:
            continue
        hub_zone = room.polygon_m.difference(walls_m.buffer(config.hub_wall_clearance_m))
        hub_zones_m[room.room_id] = hub_zone
        point_m = _safe_point(room.polygon_m, walls_m, config.hub_wall_clearance_m)
        point_units = affinity.scale(point_m, xfact=scale, yfact=scale, origin=(0, 0))
        node_id = f"N{len(nodes) + 1}"
        nodes[node_id] = Node(
            node_id=node_id,
            room_id=room.room_id,
            point_m=(float(point_m.x), float(point_m.y)),
            point_units=(float(point_units.x), float(point_units.y)),
        )
    repairs.append("Development adapter generated deterministic node points; replace with organiser fixed nodes when supplied")

    room_graph: dict[str, set[str]] = {room_id: set() for room_id, room in rooms.items() if room.eligible}
    graph = record.get("graph")
    if graph is not None:
        for source, target, attributes in graph.edges(data=True):
            if attributes.get("type") not in ZONE_EDGE_TYPES:
                continue
            source_room = component_to_room.get(source)
            target_room = component_to_room.get(target)
            if source_room in room_graph and target_room in room_graph:
                room_graph[source_room].add(target_room)
                room_graph[target_room].add(source_room)

    openings: dict[str, Opening] = {}
    for index, door_units in enumerate(_parts(geometries_units.get("door")), start=1):
        door_m = _scale_geometry(door_units, scale)
        touching = [room.room_id for room in rooms.values() if door_m.buffer(config.tolerance_m).intersects(room.polygon_m)]
        openings[f"D{index}"] = Opening(
            opening_id=f"D{index}",
            kind="door",
            rooms=touching,
            gap_polyline_m=_gap_polyline(door_m),
            counts_for_zone=True,
        )

    excluded_interior_rooms = [
        room.room_id for room in rooms.values() if not room.eligible and room.inside_inner
    ]
    return FloorPlan(
        plan_id=str(record.get("id", "unknown")),
        scale=scale,
        rooms=rooms,
        nodes=nodes,
        openings=openings,
        inner_m=inner_m,
        walls_m=walls_m,
        windows_m=windows_m,
        free_space_m=free_space_m,
        hub_zones_m=hub_zones_m,
        room_graph={room_id: sorted(neighbours) for room_id, neighbours in room_graph.items()},
        excluded_interior_rooms=excluded_interior_rooms,
        repairs=repairs,
    )


def load_resplan_dataset(path: str | Path) -> list[dict[str, Any]]:
    """Load a local ResPlan pickle for development and test selection."""

    with Path(path).open("rb") as source:
        dataset = pickle.load(source)
    if not isinstance(dataset, list):
        raise InputError("Expected a ResPlan pickle containing a list of plan records")
    return dataset


def build_floor_plan(path: str | Path, config: SolverConfig | None = None) -> FloorPlan:
    """Load one pickled ResPlan record. Organiser JSON support belongs here later."""

    with Path(path).open("rb") as source:
        record = pickle.load(source)
    if not isinstance(record, dict):
        raise InputError("Expected one pickled ResPlan plan record")
    return build_floor_plan_from_resplan(record, config)
