"""ResPlan adapter and normalisation boundary for Part 1.

Only this module knows the source record format.  The solver receives metres,
stable IDs, and explicitly labelled room links.
"""

from __future__ import annotations

import math
import pickle
from itertools import combinations
from pathlib import Path
from typing import Any

from shapely import affinity, make_valid
from shapely.errors import GEOSException
from shapely.geometry import GeometryCollection, LineString, Point
from shapely.ops import unary_union

from othello.models import FloorPlan, Node, Opening, Room, SolverConfig

ELIGIBLE_KINDS = ("living", "bedroom", "kitchen", "storage")
EXCLUDED_KINDS = ("bathroom", "balcony", "stair", "garden", "pool", "parking", "land")
EXTERIOR_KINDS = {"balcony", "garden", "pool", "parking", "land"}


class InputError(ValueError):
    pass


def _parts(geometry: Any) -> list[Any]:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "Polygon":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [part for child in geometry.geoms for part in _parts(child)]
    return []


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


def _polygonal_layer(geometry: Any, label: str, repairs: list[str], config: SolverConfig) -> Any:
    """Return a valid polygonal geometry or an empty collection.

    ``make_valid`` may return lines or points alongside repaired polygons.  The
    routing layer accepts only polygonal obstacles and rooms, so discard those
    non-area remnants at the reader boundary.
    """

    repaired = _repair(geometry, label, repairs, config)
    polygons = _parts(repaired)
    if not polygons:
        if not repaired.is_empty:
            repairs.append(f"Discarded non-polygonal remnants: {label}")
        return GeometryCollection()
    merged = unary_union(polygons)
    return _repair(merged, label, repairs, config)


def _scale_geometry(geometry: Any, scale: float) -> Any:
    return affinity.scale(geometry, xfact=1 / scale, yfact=1 / scale, origin=(0, 0))


def _graph_node_id(kind: str, index: int) -> str:
    return f"{kind}_{index}"


def _safe_point(room_m: Any, obstacles_m: Any, clearance_m: float) -> Point:
    allowed = room_m.difference(obstacles_m.buffer(clearance_m))
    if allowed.is_empty:
        raise InputError("Eligible room has no point with required wall clearance")
    return allowed.representative_point()


def _gap_polyline(geometry: Any) -> list[tuple[float, float]]:
    boundary = getattr(geometry, "boundary", None)
    if boundary is not None and hasattr(boundary, "geoms"):
        parts = [part for part in boundary.geoms if part is not None and not part.is_empty]
        boundary = max(parts, key=lambda item: item.length) if parts else None
    coordinates = list(boundary.coords) if boundary is not None and hasattr(boundary, "coords") else []
    if len(coordinates) >= 2:
        return [(float(coordinates[0][0]), float(coordinates[0][1])), (float(coordinates[1][0]), float(coordinates[1][1]))]
    centroid = geometry.representative_point()
    return [(float(centroid.x), float(centroid.y))] * 2


def _door_gap_line(
    door_m: Any,
    rooms: dict[str, Room],
    free_space_m: Any,
    tolerance_m: float,
) -> tuple[list[str], list[tuple[float, float]]] | None:
    """Build a short, free-space line through the centre of an internal door.

    Raw ResPlan doors are area polygons that overlap the wall layer.  Their
    boundary is therefore not a traversable doorway.  The short axis of the
    minimum rotated rectangle is the wall-normal direction, so extend it just
    past the door on both sides and accept it only when it joins two rooms in
    true free space.
    """

    rectangle = door_m.minimum_rotated_rectangle
    coordinates = list(rectangle.exterior.coords)
    edges = [
        (coordinates[index + 1][0] - coordinates[index][0], coordinates[index + 1][1] - coordinates[index][1])
        for index in range(len(coordinates) - 1)
    ]
    nonzero_edges = [edge for edge in edges if math.hypot(*edge) > tolerance_m]
    if not nonzero_edges:
        return None
    normal_x, normal_y = min(nonzero_edges, key=lambda edge: math.hypot(*edge))
    normal_length = math.hypot(normal_x, normal_y)
    normal_x /= normal_length
    normal_y /= normal_length
    centre = door_m.centroid
    half_depth = normal_length / 2
    extension_candidates = (2 * tolerance_m, 0.005, 0.01, 0.02, 0.05, 0.1)
    for extension in extension_candidates:
        distance = half_depth + extension
        start = (centre.x - normal_x * distance, centre.y - normal_y * distance)
        end = (centre.x + normal_x * distance, centre.y + normal_y * distance)
        gap = LineString([start, end])
        if not free_space_m.covers(gap):
            continue
        start_rooms = [room_id for room_id, room in rooms.items() if room.polygon_m.buffer(tolerance_m).covers(Point(start))]
        end_rooms = [room_id for room_id, room in rooms.items() if room.polygon_m.buffer(tolerance_m).covers(Point(end))]
        pairs = sorted({tuple(sorted((source, target))) for source in start_rooms for target in end_rooms if source != target})
        if len(pairs) == 1:
            room_ids = list(pairs[0])
            return room_ids, [(float(start[0]), float(start[1])), (float(end[0]), float(end[1]))]
    return None


def _open_passage_gap(room_a: Any, room_b: Any, walls_m: Any, windows_m: Any, wall_depth_m: float) -> Any | None:
    """Return a substantive wall-free gap between two room polygons, if one exists.

    ResPlan's ``adjacency`` graph edges also include rooms separated by an
    ordinary wall, so they are candidates only.  The geometry decides whether
    a real walkable opening exists.
    """

    if wall_depth_m <= 0:
        return None
    try:
        gap = room_a.buffer(wall_depth_m * 0.55).intersection(room_b.buffer(wall_depth_m * 0.55))
        gap = gap.difference(walls_m).difference(windows_m)
    except GEOSException:
        # A malformed candidate cannot safely establish a zone link.  It is
        # intentionally ignored instead of making the complete plan unreadable.
        return None
    if gap.is_empty:
        return None
    minimum_extent = 2 * wall_depth_m
    minimum_area = wall_depth_m * wall_depth_m * 0.25
    pieces = list(gap.geoms) if hasattr(gap, "geoms") else [gap]
    valid_pieces = []
    for piece in pieces:
        if piece.is_empty:
            continue
        min_x, min_y, max_x, max_y = piece.bounds
        if max(max_x - min_x, max_y - min_y) >= minimum_extent and piece.area >= minimum_area:
            valid_pieces.append(piece)
    return unary_union(valid_pieces) if valid_pieces else None


def _free_space(inner_m: Any, walls_m: Any, windows_m: Any, repairs: list[str]) -> Any:
    """Subtract obstacles while tolerating rare GEOS overlay failures."""

    try:
        return inner_m.difference(unary_union([walls_m, windows_m]))
    except GEOSException:
        try:
            free_space = inner_m.difference(walls_m).difference(windows_m)
            repairs.append("Constructed free space with sequential obstacle subtraction after union failure")
            return free_space
        except GEOSException:
            try:
                free_space = make_valid(inner_m).difference(make_valid(walls_m)).difference(make_valid(windows_m))
            except GEOSException as error:
                raise InputError("Unable to construct free space from repaired geometry") from error
            repairs.append("Repaired obstacle layers before sequential free-space subtraction")
            return free_space


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
            geometries_units[kind] = _polygonal_layer(geometry, kind, repairs, config)
    inner_m = _scale_geometry(inner_units, scale)
    walls_m = _scale_geometry(geometries_units.get("wall", GeometryCollection()), scale)
    windows_m = _scale_geometry(geometries_units.get("window", GeometryCollection()), scale)
    door_voids_m = unary_union(
        [
            _scale_geometry(geometries_units.get(kind, GeometryCollection()), scale)
            for kind in ("door", "front_door")
        ]
    )
    if not door_voids_m.is_empty:
        walls_m = walls_m.difference(door_voids_m)
    routing_obstacles_m = unary_union([walls_m, windows_m])
    free_space_m = _free_space(inner_m, walls_m, windows_m, repairs)

    rooms: dict[str, Room] = {}
    claimed_room_area_m = GeometryCollection()
    room_index = 1
    for kind in (*ELIGIBLE_KINDS, *EXCLUDED_KINDS):
        for index, polygon_units in enumerate(_parts(geometries_units.get(kind))):
            polygon_m = _scale_geometry(polygon_units, scale).intersection(inner_m)
            polygon_m = _repair(polygon_m, f"{kind}_{index}", repairs, config)
            polygon_m = polygon_m.difference(claimed_room_area_m)
            polygon_m = _repair(polygon_m, f"{kind}_{index}_disjoint", repairs, config)
            if polygon_m.is_empty or not _parts(polygon_m):
                repairs.append(f"Dropped room with no disjoint interior: {kind}_{index}")
                continue
            room_id = f"R{room_index:02d}"
            room_index += 1
            rooms[room_id] = Room(
                room_id=room_id,
                kind=kind,
                eligible=kind in ELIGIBLE_KINDS,
                polygon_m=polygon_m,
                polygon_units=polygon_units,
                area_m2=float(polygon_m.area),
                # ResPlan occasionally overlays exterior labels over the
                # drawing interior.  Those labels are never transit rooms in
                # the challenge, even when their clipped geometry has area.
                inside_inner=kind not in EXTERIOR_KINDS and inner_m.covers(polygon_m),
            )
            claimed_room_area_m = unary_union([claimed_room_area_m, polygon_m])

    nodes: dict[str, Node] = {}
    hub_zones_m: dict[str, Any] = {}
    for room in rooms.values():
        if not room.eligible:
            continue
        eligible_interior_m = room.polygon_m.intersection(free_space_m)
        # A hub is physically installed in free interior space.  Windows are
        # obstacles just like walls for both free-space construction and wall
        # clearance, so exclude their buffered geometry from the hub zone too.
        # Intersecting free space also guards against malformed development
        # records whose room polygon reaches beyond the inner boundary.
        # Shapely approximates circular buffer corners with line segments.
        # Add the geometry tolerance so even an inscribed buffer edge stays at
        # least the required 0.5 m from the true wall/window geometry.
        hub_clearance_buffer_m = config.hub_wall_clearance_m + config.tolerance_m
        hub_zone = eligible_interior_m.difference(routing_obstacles_m.buffer(hub_clearance_buffer_m))
        hub_zones_m[room.room_id] = hub_zone
        try:
            point_m = _safe_point(eligible_interior_m, routing_obstacles_m, config.hub_wall_clearance_m)
        except InputError:
            # A node is fixed input in the competition.  This fallback exists
            # only for ResPlan development records that lack node points; an
            # empty hub zone remains visible to territory generation.
            point_m = room.polygon_m.representative_point()
            repairs.append(f"Development node for {room.room_id} is below hub-clearance distance")
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

    wall_depth_m = float(record.get("wall_depth", 0.0)) / scale
    openings: dict[str, Opening] = {}
    for index, door_units in enumerate(_parts(geometries_units.get("door")), start=1):
        door_m = _scale_geometry(door_units, scale)
        door_gap = _door_gap_line(door_m, rooms, free_space_m, config.tolerance_m)
        if door_gap is None:
            repairs.append(f"Dropped door D{index}: no free-space connection between exactly two rooms")
            continue
        door_rooms, gap_polyline_m = door_gap
        openings[f"D{index}"] = Opening(
            opening_id=f"D{index}",
            kind="door",
            rooms=door_rooms,
            gap_polyline_m=gap_polyline_m,
            counts_for_zone=True,
        )
        source_room, target_room = door_rooms
        if source_room in room_graph and target_room in room_graph:
            room_graph[source_room].add(target_room)
            room_graph[target_room].add(source_room)

    # ResPlan's adjacency graph can contain ordinary shared walls.  Scan every
    # eligible pair geometrically so an unlabelled real opening is retained and
    # a wall-only adjacency is rejected.
    for source_room, target_room in combinations(sorted(room_graph), 2):
        if target_room in room_graph[source_room]:
            continue
        gap = _open_passage_gap(
            rooms[source_room].polygon_m,
            rooms[target_room].polygon_m,
            walls_m,
            windows_m,
            wall_depth_m,
        )
        if gap is None:
            continue
        opening_id = f"P{sum(opening.kind == 'open_passage' for opening in openings.values()) + 1}"
        openings[opening_id] = Opening(
            opening_id=opening_id,
            kind="open_passage",
            rooms=[source_room, target_room],
            gap_polyline_m=_gap_polyline(gap),
            counts_for_zone=True,
        )
        room_graph[source_room].add(target_room)
        room_graph[target_room].add(source_room)

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
