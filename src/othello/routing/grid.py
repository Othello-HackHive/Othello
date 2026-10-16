"""Task 3.1: bounded-by-deadline rectilinear graphs in normalized metres.

Inputs are duck typed against BACKEND_SCHEMA.md, so test doubles and future
shared models use the same entry point. No geometry is repaired or snapped.
Room ownership and gap semantics must be unambiguous. In particular, an
unlabelled span is supported only between two rooms with an explicit opening
crossing that span (e.g. a thick-wall doorway).
"""

from bisect import bisect_left, bisect_right
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass
from math import floor, isfinite, ulp
from numbers import Real
import time
from typing import Any

import networkx as nx
from shapely.geometry import LineString, Point
from shapely.geometry.base import BaseGeometry

from .types import Coord, GraphAttempt, GraphBuildResult, TransitMode


class _UnsupportedGeometry(Exception):
    pass


class _TimedOut(Exception):
    pass


@dataclass
class _Budget:
    deadline: float | None

    def check(self, phase: str) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _TimedOut(f"Deadline reached during {phase}; timeout is not disconnection evidence.")


@dataclass(frozen=True)
class _Opening:
    opening_id: str
    rooms: frozenset[str]
    gap: LineString


@dataclass
class _Geometry:
    inner: BaseGeometry
    free: BaseGeometry
    obstacles: tuple[BaseGeometry, ...]
    rooms: dict[str, BaseGeometry]
    excluded: tuple[BaseGeometry, ...]
    openings: tuple[_Opening, ...]
    opening_lines: tuple[LineString, ...]


def _number(value: Any, name: str, *, positive: bool = False) -> None:
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'nonnegative'}")


def _coord(value: Any, name: str) -> Coord:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise ValueError(f"{name} must be an (x, y) coordinate")
    if any(isinstance(v, bool) or not isinstance(v, Real) or not isfinite(v) for v in value):
        raise ValueError(f"{name} must have finite coordinates")
    return tuple(value)  # Preserve the input numbers; never round or snap.


def _polygonal(geom: Any, name: str, *, empty_ok: bool = False) -> None:
    if not isinstance(geom, BaseGeometry):
        raise ValueError(f"{name} must be a Shapely geometry")
    if geom.is_empty and empty_ok:
        return
    if geom.is_empty or geom.geom_type not in {"Polygon", "MultiPolygon"} or not geom.is_valid:
        raise _UnsupportedGeometry(f"{name} must be valid polygonal geometry; no repair performed")


def _read_geometry(plan: Any, budget: _Budget) -> _Geometry:
    for name in ("inner_m", "free_space_m", "walls_m", "windows_m"):
        budget.check("geometry validation")
        _polygonal(getattr(plan, name), name, empty_ok=name != "inner_m")
    rooms = {}
    for room_id, room in sorted(plan.rooms.items()):
        budget.check("geometry validation")
        _polygonal(room.polygon_m, f"room {room_id}")
        if room.inside_inner:
            rooms[room_id] = room.polygon_m
    for index, (room_id, polygon) in enumerate(rooms.items()):
        for other_id in list(rooms)[index + 1:]:
            budget.check("geometry validation")
            if polygon.intersection(rooms[other_id]).area > 0:
                raise _UnsupportedGeometry(f"Overlapping room interiors: {room_id}, {other_id}")
    excluded = []
    for room_id in plan.excluded_interior_rooms:
        if room_id not in rooms:
            raise ValueError(f"Excluded interior room {room_id!r} is missing or not interior")
        excluded.append(rooms[room_id])
    openings, opening_lines = [], []
    obstacles = tuple(g for g in (plan.walls_m, plan.windows_m) if not g.is_empty)
    for opening_id, opening in sorted(plan.openings.items()):
        budget.check("geometry validation")
        if opening.opening_id != opening_id:
            raise ValueError(f"Opening key {opening_id!r} does not match opening_id")
        if opening.kind not in {"door", "open_passage", "entrance"}:
            raise ValueError(f"Unknown opening kind: {opening.kind!r}")
        coords = [_coord(p, f"opening {opening_id}") for p in opening.gap_polyline_m]
        if len(coords) < 2:
            raise ValueError(f"Opening {opening_id} needs at least two coordinates")
        gap = LineString(coords)
        if not gap.is_valid or not gap.is_simple or gap.is_ring or gap.length == 0:
            raise _UnsupportedGeometry(f"Ambiguous gap geometry for opening {opening_id}")
        opening_lines.append(gap)
        if opening.kind == "entrance":
            continue  # Never a routing permission; outer travel is always forbidden.
        pair = frozenset(opening.rooms)
        if len(opening.rooms) != 2 or len(pair) != 2 or not pair.issubset(rooms):
            raise _UnsupportedGeometry(f"Opening {opening_id} must identify two interior rooms")
        if not plan.inner_m.covers(gap) or not plan.free_space_m.covers(gap):
            raise _UnsupportedGeometry(f"Opening {opening_id} is not covered by true free space")
        if any(gap.intersection(obstacle).length > 0 for obstacle in obstacles):
            raise _UnsupportedGeometry(f"Opening {opening_id} crosses a wall or window")
        for other in openings:
            if gap.intersection(other.gap).length > 0 or gap.crosses(other.gap):
                raise _UnsupportedGeometry(f"Ambiguous intersecting openings: {other.opening_id}, {opening_id}")
        openings.append(_Opening(opening_id, pair, gap))
    return _Geometry(plan.inner_m, plan.free_space_m, obstacles, rooms, tuple(excluded),
                     tuple(openings), tuple(opening_lines))


def _coordinates(geom: BaseGeometry):
    """Yield coordinates from polygon rings or intersection components."""
    if geom.is_empty:
        return
    if geom.geom_type == "Polygon":
        yield from geom.exterior.coords
        for ring in geom.interiors:
            yield from ring.coords
    elif hasattr(geom, "geoms"):
        for part in geom.geoms:
            yield from _coordinates(part)
    else:
        yield from geom.coords


def _coordinate_lines(plan, geometry, terminals, margin, budget):
    xs, ys = set(), set()

    def add(point):
        budget.check("coordinate generation")
        xs.add(point[0])
        ys.add(point[1])

    for point in terminals.values():
        add(point)
    for node_id, node in sorted(plan.nodes.items()):
        add(_coord(node.point_m, f"node {node_id}"))
    for point in _coordinates(geometry.inner):
        add(point)
    for gap in geometry.opening_lines:
        for point in gap.coords:
            add(point)
        add(gap.interpolate(0.5, normalized=True).coords[0])
    for obstacle in geometry.obstacles:
        for x, y in _coordinates(obstacle):
            add((x, y))
            add((x - margin, y - margin))
            add((x + margin, y + margin))
    min_x, min_y, max_x, max_y = geometry.inner.bounds
    return {x for x in xs if min_x <= x <= max_x}, {y for y in ys if min_y <= y <= max_y}


def _augment_lines(xs, ys, bounds, step, budget):
    xs, ys = set(xs), set(ys)
    for values, low, high in ((xs, bounds[0], bounds[2]), (ys, bounds[1], bounds[3])):
        if low + step == low:
            raise ValueError("grid_step_m is too small to represent at the plan coordinates")
        for index in range(floor((high - low) / step) + 1):
            budget.check("fallback coordinate generation")
            value = low + index * step
            if value <= high:
                values.add(value)
    return xs, ys


def _owner(point: Point, rooms) -> str | None:
    owners = [room_id for room_id, polygon in rooms.items() if polygon.contains(point)]
    if len(owners) > 1:
        raise _UnsupportedGeometry(f"Ambiguous room ownership at {point.wkt}")
    if any(polygon.boundary.intersects(point) for polygon in rooms.values()):
        return None
    return owners[0] if owners else None


def _spatially_allowed(shape, geometry, margin, transit_mode):
    if not geometry.inner.covers(shape) or not geometry.free.covers(shape):
        return False
    for obstacle in geometry.obstacles:
        if shape.intersects(obstacle):
            return False
        distance = shape.distance(obstacle)
        if distance < margin:
            # Coordinate subtraction and distance arithmetic can put a nominal
            # margin offset a few ULPs below the margin (e.g. 4.02 - 4).
            # This bound is solely floating-point roundoff: it never uses the
            # official geometric tolerance or scales with that tolerance.
            roundoff = 4 * max(ulp(value) for value in
                               (*shape.bounds, *obstacle.bounds, distance, margin))
            if margin - distance > roundoff:
                return False
    if transit_mode == "permitted_only" and any(shape.intersects(room) for room in geometry.excluded):
        return False
    return True


def _transverse_crossing(segment, gap, intersection):
    """A gap must pass from one side of the edge to the other, not kiss it.

    Shapely's line/line crosses predicate alone also accepts a V-shaped gap
    touching the edge at its interior vertex. Check the two local gap arms.
    Collinear intersections are rejected by the caller before this check.
    """
    if not segment.crosses(gap):
        return False
    crossing_distance = gap.project(intersection)
    axis = 1 if segment.coords[0][1] == segment.coords[-1][1] else 0
    before, after = None, None
    for coord in gap.coords:
        along = gap.project(Point(coord))
        side = coord[axis] - intersection.coords[0][axis]
        if along < crossing_distance:
            before = side
        elif along > crossing_distance:
            after = side
            break
    return before is not None and after is not None and before * after < 0


def _edge_openings(segment, geometry, tolerance, budget):
    """Return ordered crossing IDs, or None for a forbidden transition.

    Split by true room boundaries, not grid cells. Unlabelled wall-thickness
    spans are accepted only with a matching transverse opening crossing.
    """
    cuts = {0.0, segment.length}
    for polygon in geometry.rooms.values():
        budget.check("edge validation")
        intersection = segment.intersection(polygon.boundary)
        if intersection.length > 0:
            return None  # Do not run along a room boundary.
        cuts.update(segment.project(Point(p)) for p in _coordinates(intersection))
    intervals = []
    cuts = sorted(cuts)
    for left, right in zip(cuts, cuts[1:]):
        budget.check("edge validation")
        owner = _owner(segment.interpolate((left + right) / 2), geometry.rooms)
        if intervals and intervals[-1][2] == owner:
            intervals[-1] = (intervals[-1][0], right, owner)
        else:
            intervals.append((left, right, owner))
    owned = [item for item in intervals if item[2] is not None]
    if not owned or intervals[0][2] is None or intervals[-1][2] is None:
        raise _UnsupportedGeometry("Edge has unlabelled endpoint room occupancy")
    used = []
    for before, after in zip(owned, owned[1:]):
        budget.check("edge validation")
        if before[2] == after[2]:
            raise _UnsupportedGeometry(f"Unlabelled span leaves and re-enters room {before[2]}")
        pair = frozenset((before[2], after[2]))
        matching = [opening for opening in geometry.openings if opening.rooms == pair]
        if not matching:
            raise _UnsupportedGeometry(f"No opening contract for room transition {before[2]} -> {after[2]}")
        candidates = []
        for opening in geometry.openings:
            budget.check("edge validation")
            intersection = segment.intersection(opening.gap)
            if intersection.is_empty:
                continue
            in_span = [p for p in _coordinates(intersection)
                       if before[1] - tolerance <= segment.project(Point(p)) <= after[0] + tolerance]
            if not in_span:
                continue
            if opening.rooms != pair:
                raise _UnsupportedGeometry(f"Opening {opening.opening_id} disagrees with crossed rooms")
            if intersection.geom_type != "Point":
                if intersection.length > 0:
                    return None
                raise _UnsupportedGeometry(f"Multiple crossings of opening {opening.opening_id} on one transition")
            if not _transverse_crossing(segment, opening.gap, intersection):
                continue  # Gap endpoint contact/tangency is not passage.
            candidates.append(opening.opening_id)
        if len(candidates) > 1:
            raise _UnsupportedGeometry(f"Multiple openings identify transition {before[2]} -> {after[2]}")
        if not candidates:
            return None
        used.append(candidates[0])
    return tuple(used)


def _portal_pairs(vertices, geometry, tolerance, budget):
    """Keep a tolerance-offset room boundary and gap on one validated edge.

    Ordinary adjacency can insert a vertex *between* the boundary and its
    opening, making neither half a legal room transition. Add a connection
    between the nearest retained vertices strictly outside both events. These
    connections may span intermediate grid vertices, but never remove or move
    them. The caller validates the complete connection without any exceptions.
    """
    if len(vertices) < 2 or not geometry.openings:
        return
    axis = 0 if vertices[0][1] == vertices[-1][1] else 1
    positions = [vertex[axis] for vertex in vertices]
    scan = LineString((vertices[0], vertices[-1]))
    for opening in geometry.openings:
        budget.check("edge validation")
        intersection = scan.intersection(opening.gap)
        if intersection.is_empty or intersection.geom_type not in {"Point", "MultiPoint"}:
            continue
        for coord in _coordinates(intersection):
            budget.check("edge validation")
            crossing = Point(coord)
            if not _transverse_crossing(scan, opening.gap, crossing):
                continue
            at = coord[axis]
            events = [at]
            for room_id in sorted(opening.rooms):
                boundary = scan.intersection(geometry.rooms[room_id].boundary)
                events.extend(p[axis] for p in _coordinates(boundary) if abs(p[axis] - at) <= tolerance)
            left = bisect_left(positions, min(events)) - 1
            right = bisect_right(positions, max(events))
            if left >= 0 and right < len(vertices) and right > left + 1:
                yield vertices[left], vertices[right]


def _build_graph(xs, ys, geometry, terminals, config, transit_mode, budget):
    graph = nx.DiGraph()
    rows, columns = {}, {}
    # Include every terminal, even if search clearance isolates it.
    graph.add_nodes_from(terminals.values())
    for x in sorted(xs):
        for y in sorted(ys):
            budget.check("candidate enumeration")
            point = Point(x, y)
            if _owner(point, geometry.rooms) is None:
                continue
            if any(opening.gap.intersects(point) for opening in geometry.openings):
                continue
            if not _spatially_allowed(point, geometry, config.cable_wall_margin_m, transit_mode):
                continue
            vertex = (x, y)
            graph.add_node(vertex)
            rows.setdefault(y, []).append(vertex)
            columns.setdefault(x, []).append(vertex)

    def connect(start, end, forward, reverse):
        budget.check("edge validation")
        if graph.has_edge(start, end):
            return
        segment = LineString((start, end))
        if not _spatially_allowed(segment, geometry, config.cable_wall_margin_m, transit_mode):
            return
        opening_ids = _edge_openings(segment, geometry, config.tolerance_m, budget)
        if opening_ids is None:
            return
        graph.add_edge(start, end, length_m=segment.length, direction=forward, opening_ids=opening_ids)
        graph.add_edge(end, start, length_m=segment.length, direction=reverse,
                       opening_ids=tuple(reversed(opening_ids)))

    for lines, forward, reverse in ((rows, "E", "W"), (columns, "N", "S")):
        for vertices in lines.values():
            for start, end in zip(vertices, vertices[1:]):
                connect(start, end, forward, reverse)
            for start, end in _portal_pairs(vertices, geometry, config.tolerance_m, budget):
                connect(start, end, forward, reverse)
    return graph


def _connected(graph, start, goal, budget):
    budget.check("connectivity traversal")
    queue, seen = deque([start]), {start}
    while queue:
        budget.check("connectivity traversal")
        vertex = queue.popleft()
        if vertex == goal:
            return True
        for neighbor in graph.adj[vertex]:
            budget.check("connectivity traversal")
            if neighbor not in seen:
                seen.add(neighbor)
                queue.append(neighbor)
    return False


def build_routing_graph(plan, terminals: Mapping[str, Coord], start_id: str, goal_id: str,
                        config, deadline: float | None = None) -> GraphBuildResult:
    """Build/select a graph for one leg, without A* or route scoring.

    ``avoid_if_possible`` relaxes only after completed restricted Hanan AND
    augmented-grid checks. This is grid-level evidence, never proof of
    geometric infeasibility. ``cable_wall_margin_m`` is search clearance only.
    ``deadline`` is an absolute monotonic timestamp, not a duration.
    Malformed fields/configuration raise ValueError; ambiguous geometry is a
    diagnostic result. Only a ready result contains a usable graph.
    """
    attempts = []
    transit_mode = None
    fallback = False
    budget = _Budget(deadline)
    terminal_vertices = {}
    geometry_validated = False
    try:
        if deadline is not None and (not isinstance(deadline, Real) or not isfinite(deadline)):
            raise ValueError("deadline must be a finite absolute monotonic timestamp")
        _number(config.grid_step_m, "grid_step_m", positive=True)
        _number(config.cable_wall_margin_m, "cable_wall_margin_m")
        _number(config.tolerance_m, "tolerance_m")
        if config.cable_transit_policy not in {"forbid", "avoid_if_possible", "allow"}:
            raise ValueError("Unknown cable_transit_policy")
        if not isinstance(terminals, Mapping) or not terminals:
            raise ValueError("terminals must be a nonempty ID-to-coordinate mapping")
        if any(not isinstance(terminal_id, str) for terminal_id in terminals):
            raise ValueError("terminal IDs must be strings")
        for terminal_id, point in sorted(terminals.items()):
            budget.check("terminal validation")
            terminal_vertices[terminal_id] = _coord(point, f"terminal {terminal_id}")
        if start_id not in terminal_vertices or goal_id not in terminal_vertices:
            raise ValueError("start_id and goal_id must identify supplied terminals")
        geometry = _read_geometry(plan, budget)
        geometry_validated = True
        for terminal_id, coord in terminal_vertices.items():
            budget.check("terminal validation")
            point = Point(coord)
            if _owner(point, geometry.rooms) is None:
                raise _UnsupportedGeometry(f"Terminal {terminal_id} has unlabelled or boundary room ownership")
            if any(opening.gap.intersects(point) for opening in geometry.openings):
                raise _UnsupportedGeometry(f"Terminal {terminal_id} lies on an opening gap")
        xs, ys = _coordinate_lines(plan, geometry, terminal_vertices, config.cable_wall_margin_m, budget)
        modes: tuple[TransitMode, ...] = {
            "forbid": ("permitted_only",), "allow": ("allow",),
            "avoid_if_possible": ("permitted_only", "allow"),
        }[config.cable_transit_policy]
        augmented = None
        for transit_mode in modes:
            for fallback in (False, True):
                budget.check("graph construction")
                if fallback and augmented is None:
                    augmented = _augment_lines(xs, ys, geometry.inner.bounds, config.grid_step_m, budget)
                stage_xs, stage_ys = augmented if fallback else (xs, ys)
                graph = _build_graph(stage_xs, stage_ys, geometry, terminal_vertices, config, transit_mode, budget)
                start, goal = terminal_vertices[start_id], terminal_vertices[goal_id]
                # An isolated terminal node can make a self-query appear connected.
                # Check physical eligibility before any connectivity shortcut.
                eligible = all(_spatially_allowed(Point(p), geometry, config.cable_wall_margin_m, transit_mode)
                               for p in (start, goal))
                connected = eligible and _connected(graph, start, goal, budget)
                budget.check("connectivity completion")
                attempts.append(GraphAttempt(transit_mode, fallback, connected))
                if connected:
                    diagnostics = ()
                    if config.cable_transit_policy == "avoid_if_possible" and transit_mode == "allow":
                        diagnostics = ("Excluded-room transit enabled only after both permitted-only grids "
                                       "were exhausted; grid-level evidence, not geometric infeasibility.",)
                    return GraphBuildResult("ready", graph, terminal_vertices, transit_mode, fallback,
                                            tuple(attempts), diagnostics)
        status = "unreachable_on_grid"
        diagnostics = ("Requested terminals are disconnected on the completed search grids under the "
                       "configured clearance and transit policy; this is not proven geometric infeasibility.",)
    except _UnsupportedGeometry as error:
        status, diagnostics = "unsupported_geometry", (str(error),)
    except _TimedOut as error:
        status, diagnostics = "timeout", (str(error),)
    except AttributeError as error:
        raise ValueError(f"Missing or malformed BACKEND_SCHEMA field: {error}") from error
    return GraphBuildResult(status, terminal_vertices=terminal_vertices, transit_mode=transit_mode,
                            fallback_used=fallback, attempts=tuple(attempts), diagnostics=diagnostics,
                            cacheable_unsupported=status == "unsupported_geometry" and not geometry_validated)
