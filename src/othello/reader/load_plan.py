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

from shapely import affinity, make_valid, set_precision, union_all
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


def _line_parts(geometry: Any) -> list[LineString]:
    if geometry is None or geometry.is_empty:
        return []
    if geometry.geom_type == "LineString":
        return [geometry]
    if hasattr(geometry, "geoms"):
        return [part for child in geometry.geoms for part in _line_parts(child)]
    return []


def _repair(geometry: Any, label: str, repairs: list[str], config: SolverConfig) -> Any:
    if geometry is None:
        return geometry
    if geometry.is_valid:
        return geometry
    if not config.repair_geometry:
        raise InputError(f"Invalid geometry for {label}")
    # Repairing the members of an overlapping MultiPolygon separately can
    # remove their shared area.  Merge first so layered wall rectangles remain
    # solid before validity repair.
    parts = _parts(geometry)
    repaired = make_valid(unary_union(parts)) if len(parts) > 1 else make_valid(geometry)
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


def _overlay_polygonal(
    first: Any,
    second: Any,
    operation: str,
    label: str,
    repairs: list[str],
    config: SolverConfig,
) -> Any:
    try:
        result = getattr(first, operation)(second)
    except GEOSException as error:
        if not config.repair_geometry:
            raise InputError(f"Could not {operation} geometry for {label}") from error
        grid = max(config.tolerance_m / 1000, 1e-8)
        try:
            left = set_precision(make_valid(first), grid)
            right = set_precision(make_valid(second), grid)
            result = getattr(left, operation)(right)
        except GEOSException as retry_error:
            raise InputError(f"Could not {operation} repaired geometry for {label}") from retry_error
        repairs.append(f"Retried {operation} with micron-scale precision: {label}")
    return _polygonal_layer(result, label, repairs, config)


def _scale_geometry(geometry: Any, scale: float) -> Any:
    return affinity.scale(geometry, xfact=1 / scale, yfact=1 / scale, origin=(0, 0))


def _safe_point(room_m: Any, obstacles_m: Any, clearance_m: float) -> Point:
    allowed = room_m.difference(obstacles_m.buffer(clearance_m, quad_segs=64))
    if allowed.is_empty:
        raise InputError("Eligible room has no point with required wall clearance")
    return allowed.representative_point()


def _door_gap_line(
    door_m: Any,
    rooms: dict[str, Room],
    free_space_m: Any,
    tolerance_m: float,
) -> tuple[list[str], list[tuple[float, float]]] | None:
    """Build a free-space line across an internal door, in the wall plane.

    Raw ResPlan doors are area polygons that overlap the wall layer.  Their
    boundary is not the crossing contract.  Probe on both sides of the short
    (wall-normal) axis to identify the rooms, then emit the long axis.
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
    # The opening must lie across the doorway in the wall plane.  A cable
    # crosses this segment when changing rooms; a segment along the travel
    # direction would be collinear with the cable and never count as crossed.
    long_x, long_y = -normal_y, normal_x
    long_edge = max(nonzero_edges, key=lambda edge: math.hypot(*edge))
    half_width = math.hypot(*long_edge) / 2

    extension_candidates = (2 * tolerance_m, 0.005, 0.01, 0.02, 0.05, 0.1)
    for extension in extension_candidates:
        distance = half_depth + extension
        probes = [
            (centre.x - normal_x * distance, centre.y - normal_y * distance),
            (centre.x + normal_x * distance, centre.y + normal_y * distance),
        ]
        if not all(free_space_m.covers(Point(probe)) for probe in probes):
            continue
        start_rooms = [room_id for room_id, room in rooms.items() if room.polygon_m.buffer(tolerance_m).covers(Point(probes[0]))]
        end_rooms = [room_id for room_id, room in rooms.items() if room.polygon_m.buffer(tolerance_m).covers(Point(probes[1]))]
        pairs = sorted({tuple(sorted((source, target))) for source in start_rooms for target in end_rooms if source != target})
        if len(pairs) != 1:
            continue
        for trim in (2 * tolerance_m, 0.005, 0.01, 0.02):
            reach = half_width - trim
            gap = LineString(
                [
                    (centre.x - long_x * reach, centre.y - long_y * reach),
                    (centre.x + long_x * reach, centre.y + long_y * reach),
                ]
            )
            if reach > 0 and free_space_m.covers(gap):
                return list(pairs[0]), [(float(x), float(y)) for x, y in gap.coords]
    return None


def _open_passage_gap(
    room_a: Any,
    room_b: Any,
    walls_m: Any,
    windows_m: Any,
    free_space_m: Any,
    wall_depth_m: float,
    tolerance_m: float,
) -> LineString | None:
    """Return a substantive wall-free crossing line between two rooms.

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
    if not valid_pieces:
        return None

    def joins_rooms(line: LineString) -> bool:
        start, end = line.coords[0], line.coords[-1]
        length = line.length
        if length == 0:
            return False
        normal_x = -(end[1] - start[1]) / length
        normal_y = (end[0] - start[0]) / length
        middle = line.interpolate(0.5, normalized=True)
        for distance in (2 * tolerance_m, 0.01, 0.05, wall_depth_m, 2 * wall_depth_m):
            probes = [
                Point(middle.x - normal_x * distance, middle.y - normal_y * distance),
                Point(middle.x + normal_x * distance, middle.y + normal_y * distance),
            ]
            if not all(free_space_m.covers(point) for point in probes):
                continue
            if not free_space_m.covers(LineString(probes)):
                continue
            if (room_a.covers(probes[0]) and room_b.covers(probes[1])) or (
                room_b.covers(probes[0]) and room_a.covers(probes[1])
            ):
                return True
        return False

    # Where room polygons touch, their shared boundary is the natural
    # transverse crossing line.  Select a straight segment of that boundary
    # that lies in the candidate opening and in true free space.  The old
    # arbitrary first edge could be millimetres long or cross a wall.
    shared = room_a.boundary.intersection(room_b.boundary)
    shared = shared.intersection(unary_union(valid_pieces)).intersection(free_space_m)
    minimum_length = max(2 * wall_depth_m, 0.1)
    segments: list[LineString] = []
    for line in _line_parts(shared):
        for start, end in zip(line.coords, list(line.coords)[1:]):
            segment = LineString([start, end])
            if segment.length >= minimum_length and free_space_m.covers(segment):
                segments.append(segment)
    if segments:
        longest = max(segments, key=lambda segment: segment.length)
        trim = min(2 * tolerance_m, longest.length / 4)
        result = LineString([longest.interpolate(trim), longest.interpolate(longest.length - trim)])
        if free_space_m.covers(result) and joins_rooms(result):
            return result

    # Non-touching room polygons can still have a narrow wall-free span.
    # The candidate's longest rectangle axis supplies a transverse line;
    # reject it if it is too short or not fully in free space.
    for piece in sorted(valid_pieces, key=lambda item: item.area, reverse=True):
        rectangle = piece.minimum_rotated_rectangle
        coordinates = list(rectangle.exterior.coords)
        edges = [
            (coordinates[index + 1][0] - coordinates[index][0], coordinates[index + 1][1] - coordinates[index][1])
            for index in range(len(coordinates) - 1)
        ]
        if not edges:
            continue
        long_x, long_y = max(edges, key=lambda edge: math.hypot(*edge))
        length = math.hypot(long_x, long_y)
        if length < minimum_length:
            continue
        long_x /= length
        long_y /= length
        centre = piece.centroid
        for trim in (2 * tolerance_m, 0.005, 0.01, 0.02):
            reach = length / 2 - trim
            if reach <= 0:
                continue
            result = LineString([
                (centre.x - long_x * reach, centre.y - long_y * reach),
                (centre.x + long_x * reach, centre.y + long_y * reach),
            ])
            if free_space_m.covers(result) and joins_rooms(result):
                return result
    return None


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
    room_parts_units: dict[str, list[Any]] = {}
    door_parts_units: list[Any] = []
    for kind in (*ELIGIBLE_KINDS, *EXCLUDED_KINDS, "wall", "window", "door", "front_door"):
        geometry = record.get(kind)
        if geometry is not None:
            if kind in (*ELIGIBLE_KINDS, *EXCLUDED_KINDS):
                # Each source component represents a room.  Unioning adjacent
                # same-kind components would silently remove a room and node.
                room_parts_units[kind] = [
                    _polygonal_layer(part, f"{kind}_{index}", repairs, config)
                    for index, part in enumerate(_parts(geometry))
                ]
            elif kind == "door":
                if not geometry.is_valid and not config.repair_geometry:
                    raise InputError("Invalid geometry for door")
                # Door polygons may overlap at a corner.  Their union is right
                # for cutting wall voids, but it can turn two separate doors
                # into one L-shaped opening with the wrong orientation.
                for index, raw_part in enumerate(_parts(geometry), start=1):
                    repaired_part = _polygonal_layer(raw_part, f"door_{index}", repairs, config)
                    door_parts_units.extend(_parts(repaired_part))
                geometries_units[kind] = unary_union(door_parts_units) if door_parts_units else GeometryCollection()
            else:
                geometries_units[kind] = _polygonal_layer(geometry, kind, repairs, config)
    room_precision_m = max(config.tolerance_m / 1000, 1e-8)
    inner_m = set_precision(_scale_geometry(inner_units, scale), room_precision_m)
    walls_m = _scale_geometry(geometries_units.get("wall", GeometryCollection()), scale)
    windows_m = _scale_geometry(geometries_units.get("window", GeometryCollection()), scale)
    door_voids_m = unary_union(
        [
            _scale_geometry(geometries_units.get(kind, GeometryCollection()), scale)
            for kind in ("door", "front_door")
        ]
    )
    if not door_voids_m.is_empty:
        walls_m = _overlay_polygonal(
            walls_m, door_voids_m, "difference", "wall after door voids", repairs, config,
        )
        # Boolean subtraction can make an otherwise valid wall layer invalid
        # when raw door rectangles touch or overlap.  Part 3 deliberately
        # refuses invalid geometry, so normalise the resulting obstacle layer
        # before it becomes shared routing input.
        walls_m = _polygonal_layer(walls_m, "wall after door voids", repairs, config)
    routing_obstacles_m = unary_union([walls_m, windows_m])
    free_space_m = _free_space(inner_m, walls_m, windows_m, repairs)

    # Give a smaller enclosed room its own area before subtracting overlaps
    # from a broad living-room polygon.  Keep public IDs in source order.
    room_candidates: list[tuple[str, int, Any, Any]] = []
    for kind in (*ELIGIBLE_KINDS, *EXCLUDED_KINDS):
        for index, polygon_units in enumerate(room_parts_units.get(kind, [])):
            polygon_m = set_precision(
                _overlay_polygonal(
                    _scale_geometry(polygon_units, scale), inner_m, "intersection",
                    f"{kind}_{index}_interior", repairs, config,
                ),
                room_precision_m,
            )
            room_candidates.append((kind, index, polygon_units, polygon_m))

    claimed_room_area_m = GeometryCollection()
    owned_polygons: dict[tuple[str, int], Any] = {}
    for kind, index, _, polygon_m in sorted(
        room_candidates,
        key=lambda item: (item[0] in EXTERIOR_KINDS, item[3].area, (*ELIGIBLE_KINDS, *EXCLUDED_KINDS).index(item[0]), item[1]),
    ):
        if polygon_m.is_empty:
            owned_polygons[kind, index] = polygon_m
            continue
        owned = set_precision(
            _overlay_polygonal(
                polygon_m, claimed_room_area_m, "difference", f"{kind}_{index}_disjoint", repairs, config,
            ),
            room_precision_m,
        )
        owned_polygons[kind, index] = owned
        if not owned.is_empty:
            try:
                claimed_room_area_m = union_all([claimed_room_area_m, owned], grid_size=room_precision_m)
            except GEOSException:
                grid = max(config.tolerance_m / 1000, 1e-8)
                claimed_room_area_m = union_all([claimed_room_area_m, owned], grid_size=grid)
                repairs.append(f"Retried room union with micron-scale precision: {kind}_{index}")
            claimed_room_area_m = _polygonal_layer(
                claimed_room_area_m, f"claimed after {kind}_{index}", repairs, config,
            )

    rooms: dict[str, Room] = {}
    room_index = 1
    for kind, index, polygon_units, _ in room_candidates:
        # A common micron-scale precision model removes zero-area overlay
        # slivers that GEOS can otherwise report as a positive room overlap.
        polygon_m = owned_polygons[kind, index]
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
            # Exterior labels never become transit rooms.  Every other source
            # room has already been clipped to the inner boundary.
            inside_inner=kind not in EXTERIOR_KINDS,
        )

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
        # Use fine arcs plus the geometry tolerance so even an inscribed edge
        # stays at least the required 0.5 m from the true obstacle geometry.
        hub_clearance_buffer_m = config.hub_wall_clearance_m + config.tolerance_m
        hub_zone = eligible_interior_m.difference(
            routing_obstacles_m.buffer(hub_clearance_buffer_m, quad_segs=64)
        )
        # Overlay operations can leave a zero-area boundary sliver outside
        # the source room/free space.  Inset only when strict containment
        # fails, in tiny steps far below the rule tolerance.
        for _ in range(8):
            if hub_zone.is_empty or (
                room.polygon_m.covers(hub_zone)
                and inner_m.covers(hub_zone)
                and free_space_m.covers(hub_zone)
            ):
                break
            hub_zone = hub_zone.buffer(-config.tolerance_m / 1000)
        if not hub_zone.is_empty and not (
            room.polygon_m.covers(hub_zone)
            and inner_m.covers(hub_zone)
            and free_space_m.covers(hub_zone)
        ):
            raise InputError(f"Could not confine hub zone to free interior for {room.room_id}")
        hub_zones_m[room.room_id] = hub_zone
        try:
            point_m = _safe_point(eligible_interior_m, routing_obstacles_m, config.hub_wall_clearance_m)
        except InputError:
            # A node is fixed input in the competition.  This fallback exists
            # only for ResPlan development records that lack node points; an
            # empty hub zone remains visible to territory generation.
            point_m = (
                eligible_interior_m.representative_point()
                if not eligible_interior_m.is_empty else room.polygon_m.representative_point()
            )
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
    for index, door_units in enumerate(door_parts_units, start=1):
        door_m = _scale_geometry(door_units, scale)
        door_gap = _door_gap_line(door_m, rooms, free_space_m, config.tolerance_m)
        if door_gap is None:
            repairs.append(f"Dropped door D{index}: no free-space connection between exactly two rooms")
            continue
        door_rooms, gap_polyline_m = door_gap
        if not all(rooms[room_id].inside_inner for room_id in door_rooms):
            repairs.append(f"Dropped door D{index}: connection includes a non-interior room")
            continue
        new_gap = LineString(gap_polyline_m)
        conflicts = [
            opening_id for opening_id, opening in openings.items()
            if opening.kind == "door" and (
                new_gap.intersection(LineString(opening.gap_polyline_m)).length > 0
                or new_gap.crosses(LineString(opening.gap_polyline_m))
            )
        ]
        if conflicts:
            if any(set(openings[opening_id].rooms) != set(door_rooms) for opening_id in conflicts):
                repairs.append(f"Dropped door D{index}: ambiguous overlap with another room pair")
                continue
            if any(LineString(openings[opening_id].gap_polyline_m).length >= new_gap.length for opening_id in conflicts):
                repairs.append(f"Dropped door D{index}: duplicate crossing contract")
                continue
            for opening_id in conflicts:
                del openings[opening_id]
                repairs.append(f"Dropped door {opening_id}: replaced by longer duplicate D{index}")
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
            free_space_m,
            wall_depth_m,
            config.tolerance_m,
        )
        if gap is None:
            continue
        gap_polyline_m = [(float(x), float(y)) for x, y in gap.coords]
        if any(
            gap.intersection(LineString(opening.gap_polyline_m)).length > 0
            or gap.crosses(LineString(opening.gap_polyline_m))
            for opening in openings.values()
        ):
            repairs.append(f"Dropped inferred passage {source_room}-{target_room}: intersects another opening")
            continue
        opening_id = f"P{sum(opening.kind == 'open_passage' for opening in openings.values()) + 1}"
        openings[opening_id] = Opening(
            opening_id=opening_id,
            kind="open_passage",
            rooms=[source_room, target_room],
            gap_polyline_m=gap_polyline_m,
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
