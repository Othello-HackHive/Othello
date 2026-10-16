"""Deterministic hub samples from Part 1's per-room feasible regions."""

from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import math
import time
from typing import Any, Iterator

from shapely.errors import GEOSException
from shapely.geometry import LineString, Point, Polygon, MultiPolygon
from shapely.geometry.base import BaseGeometry
from shapely.ops import nearest_points, polylabel, unary_union

from .types import Coord, HubCandidate, HubCandidateResult


class _TimedOut(Exception):
    pass


class _Unsupported(Exception):
    pass


@dataclass(slots=True)
class _Sample:
    room_id: str
    point_m: Coord
    tags: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class _DoorGroup:
    room_id: str
    opening_id: str
    gap: LineString
    threshold_m: float


_TAG_ORDER = {"node_exact": 0, "deepest": 1, "near_door": 2, "node_aligned": 3, "grid": 4}


def _check_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise _TimedOut


def _roundoff(*values: float) -> float:
    """A few representable steps, never the official 1 mm geometry tolerance."""
    finite = [abs(value) for value in values if math.isfinite(value)]
    return 8.0 * max((math.ulp(value) for value in finite), default=math.ulp(1.0))


def _coord(value: Any, label: str) -> Coord:
    try:
        if len(value) != 2:
            raise ValueError
        x, y = float(value[0]), float(value[1])
    except (TypeError, ValueError, IndexError, KeyError) as exc:
        raise _Unsupported(f"{label} must be a two-coordinate metre point") from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise _Unsupported(f"{label} has non-finite coordinates")
    return x, y


def _geometry(value: Any, label: str, *, polygonal: bool = False) -> BaseGeometry:
    if not isinstance(value, BaseGeometry) or not value.is_valid:
        raise _Unsupported(f"{label} is missing or is not valid Shapely geometry")
    if polygonal and not value.is_empty and not isinstance(value, (Polygon, MultiPolygon)):
        raise _Unsupported(f"{label} must be polygonal")
    return value


def _parts(zone: BaseGeometry) -> tuple[Polygon, ...]:
    if zone.is_empty:
        return ()
    if isinstance(zone, Polygon):
        return (zone,)
    return tuple(sorted(zone.geoms, key=lambda p: (p.bounds, p.area)))


def _grid_values(low: float, high: float, step: float) -> Iterator[float]:
    first = math.ceil(low / step)
    last = math.floor(high / step)
    for index in range(first, last + 1):
        yield index * step


def _validate_config(config: Any, deadline: float | None) -> tuple[float, float, float, int]:
    _check_deadline(deadline)
    try:
        step = float(config.hub_grid_step_m)
        clearance = float(config.hub_wall_clearance_m)
        tolerance = float(config.tolerance_m)
        cap = config.limits.hub_candidates
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("config needs hub_grid_step_m, hub_wall_clearance_m, tolerance_m and limits.hub_candidates") from exc
    if not math.isfinite(step) or step <= 0:
        raise ValueError("hub_grid_step_m must be finite and positive")
    if not math.isfinite(clearance) or clearance < 0.5:
        raise ValueError("hub_wall_clearance_m must be finite and at least the official 0.5 m")
    if not math.isfinite(tolerance) or tolerance <= 0:
        raise ValueError("tolerance_m must be finite and positive")
    if isinstance(cap, bool) or not isinstance(cap, int) or cap <= 0:
        raise ValueError("limits.hub_candidates must be a positive integer")
    _check_deadline(deadline)
    return step, clearance, tolerance, cap


def _opening_gap(opening: Any, opening_id: str) -> LineString:
    try:
        points = [_coord(point, f"opening {opening_id} gap") for point in opening.gap_polyline_m]
        gap = LineString(points)
    except (AttributeError, TypeError, ValueError) as exc:
        raise _Unsupported(f"opening {opening_id} has ambiguous gap geometry") from exc
    if len(points) < 2 or gap.is_empty or gap.length <= 0 or not gap.is_valid or not gap.is_simple:
        raise _Unsupported(f"opening {opening_id} has ambiguous gap geometry")
    return gap


def _sample_key(sample: _Sample) -> tuple:
    return (sample.room_id, min(_TAG_ORDER[tag] for tag in sample.tags), *sample.point_m)


def _deduplicate(raw: list[_Sample], tolerance: float, deadline: float | None) -> list[_Sample]:
    """Pick a stable exact input point for each within-tolerance room cluster."""
    buckets: dict[tuple[str, int, int], list[int]] = {}
    kept: list[_Sample] = []
    for sample in sorted(raw, key=_sample_key):
        _check_deadline(deadline)
        x, y = sample.point_m
        cell_x, cell_y = math.floor(x / tolerance), math.floor(y / tolerance)
        matches: list[int] = []
        for dx in (-1, 0, 1):
            for dy in (-1, 0, 1):
                for index in buckets.get((sample.room_id, cell_x + dx, cell_y + dy), ()):
                    px, py = kept[index].point_m
                    if math.hypot(x - px, y - py) <= tolerance + _roundoff(x, y, px, py, tolerance):
                        matches.append(index)
        if matches:
            kept[min(matches)].tags.update(sample.tags)
        else:
            index = len(kept)
            kept.append(_Sample(sample.room_id, sample.point_m, set(sample.tags)))
            buckets.setdefault((sample.room_id, cell_x, cell_y), []).append(index)
    return kept


def _attachment_proxy(point: Coord, node_points: tuple[Coord, ...]) -> float:
    x, y = point
    distances = sorted(abs(x - nx) + abs(y - ny) for nx, ny in node_points)
    return 2 * distances[0] if len(distances) == 1 else distances[0] + distances[1]


def _minimum_cover(
    candidates: list[HubCandidate], masks: list[int], required: int, deadline: float | None
) -> tuple[int, ...] | None:
    """Exact minimum set cover; identical masks need only their cheapest candidate."""
    best_for_mask: dict[int, int] = {}
    for index, mask in enumerate(masks):
        _check_deadline(deadline)
        if not mask:
            continue
        old = best_for_mask.get(mask)
        key = (candidates[index].attachment_proxy_m, candidates[index].room_id, candidates[index].point_m)
        if old is None or key < (
            candidates[old].attachment_proxy_m, candidates[old].room_id, candidates[old].point_m
        ):
            best_for_mask[mask] = index
    options = tuple(sorted(best_for_mask.values(), key=lambda i: (
        candidates[i].attachment_proxy_m, candidates[i].room_id, candidates[i].point_m
    )))
    for bit in range(required.bit_length()):
        if required & (1 << bit) and not any(masks[i] & (1 << bit) for i in options):
            return None

    @lru_cache(maxsize=None)
    def solve(uncovered: int) -> tuple[int, ...] | None:
        _check_deadline(deadline)
        if not uncovered:
            return ()
        bits = [bit for bit in range(uncovered.bit_length()) if uncovered & (1 << bit)]
        target = min(bits, key=lambda bit: sum(bool(masks[i] & (1 << bit)) for i in options))
        answer: tuple[int, ...] | None = None
        answer_key: tuple | None = None
        for index in options:
            _check_deadline(deadline)
            if not masks[index] & (1 << target):
                continue
            tail = solve(uncovered & ~masks[index])
            if tail is None:
                continue
            selection = tuple(sorted((index, *tail)))
            key = (
                len(selection),
                sum(candidates[i].attachment_proxy_m for i in selection),
                tuple((candidates[i].room_id, candidates[i].point_m) for i in selection),
            )
            if answer_key is None or key < answer_key:
                answer, answer_key = selection, key
        return answer

    return solve(required)


def generate_hub_candidates(
    plan: Any, territory: Any, config: Any, deadline: float | None = None
) -> HubCandidateResult:
    """Generate feasible, diverse candidates; never change Part 1 geometry."""
    try:
        step, clearance, tolerance, cap = _validate_config(config, deadline)
        _check_deadline(deadline)
        try:
            room_ids = tuple(sorted(territory.room_ids))
            node_ids = tuple(sorted(territory.node_ids))
            rooms = plan.rooms
            nodes = plan.nodes
            zones = plan.hub_zones_m
            openings = plan.openings
        except (AttributeError, TypeError) as exc:
            raise _Unsupported("missing Part 1 territory or floor-plan fields") from exc
        if not room_ids or len(set(room_ids)) != len(room_ids) or not node_ids or len(set(node_ids)) != len(node_ids):
            raise _Unsupported("territory needs distinct assigned rooms and nodes")
        inner = _geometry(getattr(plan, "inner_m", None), "inner_m", polygonal=True)
        free = _geometry(getattr(plan, "free_space_m", None), "free_space_m", polygonal=True)
        walls = _geometry(getattr(plan, "walls_m", None), "walls_m", polygonal=True)
        territory_zone = _geometry(getattr(territory, "hub_zone_m", None), "territory.hub_zone_m", polygonal=True)
        room_polygons: dict[str, BaseGeometry] = {}
        room_zones: dict[str, BaseGeometry] = {}
        all_room_polygons: dict[str, BaseGeometry] = {}
        for room_id, room in rooms.items():
            _check_deadline(deadline)
            all_room_polygons[room_id] = _geometry(getattr(room, "polygon_m", None),
                                                    f"room {room_id}", polygonal=True)
        for room_id in room_ids:
            _check_deadline(deadline)
            if room_id not in rooms or room_id not in zones:
                raise _Unsupported(f"room {room_id} or its hub zone is missing")
            room = rooms[room_id]
            if not getattr(room, "eligible", False) or not getattr(room, "inside_inner", False):
                raise _Unsupported(f"room {room_id} is not an assigned eligible interior room")
            polygon = all_room_polygons[room_id]
            zone = _geometry(zones[room_id], f"hub zone {room_id}", polygonal=True)
            if polygon.is_empty or (not zone.is_empty and (
                not polygon.covers(zone) or not inner.covers(zone) or not free.covers(zone)
            )):
                raise _Unsupported(f"hub zone {room_id} extends outside its room or free interior")
            room_polygons[room_id], room_zones[room_id] = polygon, zone
        for room_id in room_ids:
            for other_id, other_polygon in all_room_polygons.items():
                _check_deadline(deadline)
                if other_id != room_id and room_zones[room_id].intersection(other_polygon).area > 0:
                    raise _Unsupported(f"rooms {room_id} and {other_id} have ambiguous ownership")
        expected_union = unary_union(list(room_zones.values()))
        _check_deadline(deadline)
        if not expected_union.equals(territory_zone):
            raise _Unsupported("territory.hub_zone_m differs from the union of assigned per-room zones")
        node_points: list[Coord] = []
        for node_id in node_ids:
            _check_deadline(deadline)
            if node_id not in nodes or nodes[node_id].room_id not in room_ids:
                raise _Unsupported(f"node {node_id} is missing or belongs to another territory")
            node_points.append(_coord(nodes[node_id].point_m, f"node {node_id}"))
        ordered_nodes = tuple(node_points)

        doors: list[_DoorGroup] = []
        for opening_id in sorted(openings):
            _check_deadline(deadline)
            opening = openings[opening_id]
            try:
                kind, counts_for_zone, opening_rooms = opening.kind, opening.counts_for_zone, opening.rooms
            except AttributeError as exc:
                raise _Unsupported(f"opening {opening_id} is missing ownership or type metadata") from exc
            if kind not in {"door", "open_passage", "entrance"}:
                raise _Unsupported(f"opening {opening_id} has an unknown kind")
            if kind == "entrance":
                if counts_for_zone:
                    raise _Unsupported(f"entrance {opening_id} cannot count for a room zone")
                continue
            if not counts_for_zone:
                raise _Unsupported(f"opening {opening_id} must count for room zones")
            try:
                related = set(opening_rooms) & set(room_ids)
            except TypeError as exc:
                raise _Unsupported(f"opening {opening_id} has ambiguous room ownership") from exc
            if not related:
                continue
            if len(opening_rooms) != 2 or len(set(opening_rooms)) != 2:
                raise _Unsupported(f"opening {opening_id} has ambiguous room ownership")
            gap = _opening_gap(opening, opening_id)
            for room_id in sorted(related):
                if not room_zones[room_id].is_empty:
                    threshold = room_zones[room_id].distance(gap) + step
                    doors.append(_DoorGroup(room_id, opening_id, gap, threshold))

        raw: list[_Sample] = []
        deepest: dict[str, list[tuple[Polygon, Coord]]] = {}

        def add(room_id: str, point: Coord, tag: str) -> None:
            _check_deadline(deadline)
            x, y = point
            if not math.isfinite(x) or not math.isfinite(y):
                return
            p = Point(point)
            zone = room_zones[room_id]
            if not room_polygons[room_id].contains(p) or not zone.covers(p):
                return
            if not inner.covers(p) or not free.covers(p):
                return
            if not walls.is_empty:
                distance = p.distance(walls)
                if distance + _roundoff(distance, clearance, x, y) < clearance:
                    return
            raw.append(_Sample(room_id, point, {tag}))

        for room_id in room_ids:
            _check_deadline(deadline)
            zone = room_zones[room_id]
            if zone.is_empty:
                continue
            components: list[tuple[Polygon, Coord]] = []
            for component in _parts(zone):
                _check_deadline(deadline)
                deep = polylabel(component, tolerance=min(step / 10, 0.01))
                point = (deep.x, deep.y)
                components.append((component, point))
                add(room_id, point, "deepest")
            deepest[room_id] = components
            minx, miny, maxx, maxy = zone.bounds
            for x in _grid_values(minx, maxx, step):
                for y in _grid_values(miny, maxy, step):
                    add(room_id, (x, y), "grid")
            for nx, ny in ordered_nodes:
                for y in _grid_values(miny, maxy, step):
                    add(room_id, (nx, y), "node_aligned")
                for x in _grid_values(minx, maxx, step):
                    add(room_id, (x, ny), "node_aligned")
                add(room_id, (nx, ny), "node_exact")
            for nx, _ in ordered_nodes:
                for _, ny in ordered_nodes:
                    add(room_id, (nx, ny), "node_aligned")

        for door in doors:
            _check_deadline(deadline)
            for component, deep in deepest[door.room_id]:
                _check_deadline(deadline)
                near, _ = nearest_points(component, door.gap)
                nearest = (near.x, near.y)
                add(door.room_id, nearest, "near_door")
                dx, dy = deep[0] - near.x, deep[1] - near.y
                distance = math.hypot(dx, dy)
                if distance:
                    for travel in (step / 16, step / 4, step / 2, step):
                        fraction = min(travel / distance, 1.0)
                        add(door.room_id, (near.x + fraction * dx, near.y + fraction * dy), "near_door")

        samples = _deduplicate(raw, tolerance, deadline)
        if not samples:
            _check_deadline(deadline)
            return HubCandidateResult("no_candidates", diagnostics=("No valid points in assigned hub zones",))
        candidates: list[HubCandidate] = []
        for sample in samples:
            _check_deadline(deadline)
            point = Point(sample.point_m)
            nearby = tuple(sorted(door.opening_id for door in doors if door.room_id == sample.room_id and
                                  point.distance(door.gap) <= door.threshold_m +
                                  _roundoff(*sample.point_m, door.threshold_m)))
            candidates.append(HubCandidate(
                sample.point_m, sample.room_id,
                tuple(sorted(sample.tags, key=lambda tag: _TAG_ORDER[tag])), nearby,
                _attachment_proxy(sample.point_m, ordered_nodes),
            ))

        mandatory: set[int] = set()
        for room_id in room_ids:
            _check_deadline(deadline)
            if room_zones[room_id].is_empty:
                continue
            own = [i for i, candidate in enumerate(candidates) if candidate.room_id == room_id]
            if not own:
                _check_deadline(deadline)
                return HubCandidateResult("no_candidates", diagnostics=(f"No valid candidate in room {room_id}",))
            own_doors = [door for door in doors if door.room_id == room_id]
            masks = [1 | sum(1 << (index + 1) for index, door in enumerate(own_doors)
                             if door.opening_id in candidates[i].nearby_opening_ids) for i in own]
            solution = _minimum_cover([candidates[i] for i in own], masks,
                                      (1 << (len(own_doors) + 1)) - 1, deadline)
            if solution is None:
                _check_deadline(deadline)
                return HubCandidateResult("no_candidates", diagnostics=(
                    f"No generated candidates cover every doorway group in room {room_id}",
                ))
            mandatory.update(own[index] for index in solution)
        _check_deadline(deadline)
        if len(mandatory) > cap:
            raise ValueError(f"limits.hub_candidates={cap} is below the {len(mandatory)} distinct required representatives")

        selected = set(mandatory)
        for tag in ("deepest", "node_aligned"):
            for room_id in room_ids:
                _check_deadline(deadline)
                if len(selected) >= cap:
                    break
                options = [i for i, c in enumerate(candidates) if i not in selected and
                           c.room_id == room_id and tag in c.source_tags]
                if options:
                    chosen = min(options, key=lambda i: (candidates[i].attachment_proxy_m, candidates[i].point_m))
                    selected.add(chosen)
        while len(selected) < min(cap, len(candidates)):
            _check_deadline(deadline)
            added = False
            for room_id in room_ids:
                _check_deadline(deadline)
                if len(selected) >= cap:
                    break
                options = [i for i, c in enumerate(candidates) if i not in selected and c.room_id == room_id]
                if not options:
                    continue
                own_selected = [candidates[i].point_m for i in selected if candidates[i].room_id == room_id]
                def separation(index: int) -> float:
                    x, y = candidates[index].point_m
                    return min(math.hypot(x - sx, y - sy) for sx, sy in own_selected)
                chosen = min(options, key=lambda i: (-separation(i), candidates[i].attachment_proxy_m,
                                                      candidates[i].point_m))
                selected.add(chosen)
                added = True
            if not added:
                break
        _check_deadline(deadline)
        output = tuple(sorted((candidates[i] for i in selected),
                              key=lambda candidate: (candidate.attachment_proxy_m, candidate.room_id,
                                                     candidate.point_m)))
        return HubCandidateResult("ready", output)
    except _TimedOut:
        return HubCandidateResult("timeout", diagnostics=("Hub candidate deadline expired",))
    except _Unsupported as exc:
        return HubCandidateResult("unsupported_geometry", diagnostics=(str(exc),))
    except GEOSException as exc:
        return HubCandidateResult("unsupported_geometry", diagnostics=(f"GEOS could not process hub geometry: {exc}",))
