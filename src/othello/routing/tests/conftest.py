"""Local stand-ins only: no production shared models or root test configuration."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from shapely.geometry import GeometryCollection, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))


def make_config(**overrides):
    # Coarser fallback keeps unit cases small; the builder supplies no defaults.
    values = dict(grid_step_m=1.0, cable_wall_margin_m=0.02, tolerance_m=0.001,
                  cable_transit_policy="avoid_if_possible")
    values.update(overrides)
    return SimpleNamespace(**values)


def make_room(room_id, polygon, *, excluded=False, inside_inner=True):
    return SimpleNamespace(room_id=room_id, polygon_m=polygon,
                           eligible=not excluded, inside_inner=inside_inner)


def make_opening(opening_id, rooms, gap, kind="door"):
    return SimpleNamespace(opening_id=opening_id, rooms=rooms, gap_polyline_m=gap,
                           kind=kind, counts_for_zone=kind != "entrance")


def make_plan(*, inner=None, rooms=None, walls=None, windows=None, openings=(), excluded=(), nodes=None):
    inner = box(0, 0, 10, 10) if inner is None else inner
    walls = GeometryCollection() if walls is None else walls
    windows = GeometryCollection() if windows is None else windows
    rooms = [make_room("R1", inner)] if rooms is None else rooms
    return SimpleNamespace(
        plan_id="mock", inner_m=inner, walls_m=walls, windows_m=windows,
        free_space_m=inner.difference(unary_union([walls, windows])),
        rooms={room.room_id: room for room in rooms},
        openings={opening.opening_id: opening for opening in openings},
        excluded_interior_rooms=list(excluded), nodes=nodes or {},
    )


@pytest.fixture
def plan():
    return make_plan()


@pytest.fixture
def config():
    return make_config()


@pytest.fixture
def terminals():
    return {"H1": (1.125, 2.375), "N1": (8.625, 7.125)}


@pytest.fixture
def assert_edges_safe():
    """Independent checks using Shapely directly, not the routing predicate."""
    from shapely.geometry import LineString

    def check(result, plan, config):
        assert result.status == "ready", result.diagnostics
        for start, end, data in result.graph.edges(data=True):
            assert (start[0] == end[0]) != (start[1] == end[1])
            segment = LineString([start, end])
            assert plan.inner_m.covers(segment)
            assert plan.free_space_m.covers(segment)
            assert data["length_m"] == segment.length > 0
            for obstacle in (plan.walls_m, plan.windows_m):
                if not obstacle.is_empty:
                    assert segment.disjoint(obstacle)
                    distance = segment.distance(obstacle)
                    # Only machine-scale test roundoff, never the 1 mm rule tolerance.
                    assert distance >= config.cable_wall_margin_m or distance == pytest.approx(
                        config.cable_wall_margin_m, rel=0, abs=1e-12)
            if result.transit_mode == "permitted_only":
                for room_id in plan.excluded_interior_rooms:
                    assert segment.disjoint(plan.rooms[room_id].polygon_m)
            expected = ("E" if end[0] > start[0] else "W") if start[1] == end[1] else (
                "N" if end[1] > start[1] else "S")
            assert data["direction"] == expected
            reverse = result.graph[end][start]
            assert reverse["opening_ids"] == tuple(reversed(data["opening_ids"]))
            assert reverse["length_m"] == data["length_m"]
            for opening_id in data["opening_ids"]:
                assert segment.crosses(LineString(plan.openings[opening_id].gap_polyline_m))
    return check


@pytest.fixture
def assert_room_transitions_authorized():
    """Derive room intervals from polygon intersections, ignoring edge metadata.

    This oracle uses no routing helpers. It also verifies metadata completeness
    after independently establishing the transitions and their permissions.
    """
    from shapely.geometry import LineString, Point

    def linear_parts(geom):
        if geom.is_empty:
            return
        if geom.geom_type == "LineString":
            yield geom
        elif hasattr(geom, "geoms"):
            for child in geom.geoms:
                yield from linear_parts(child)

    def check(graph, plan, config):
        for start, end, data in graph.edges(data=True):
            segment = LineString([start, end])
            intervals = []
            for room_id, room in plan.rooms.items():
                if not room.inside_inner:
                    continue
                for portion in linear_parts(segment.intersection(room.polygon_m)):
                    if portion.length > 0 and room.polygon_m.contains(portion.interpolate(0.5, normalized=True)):
                        ends = [segment.project(Point(portion.coords[i])) for i in (0, -1)]
                        intervals.append((min(ends), max(ends), room_id))
            intervals.sort()
            authorized_ids = []
            for before, after in zip(intervals, intervals[1:]):
                if before[2] == after[2]:
                    continue
                allowed = []
                for opening_id, opening in plan.openings.items():
                    if opening.kind not in {"door", "open_passage"}:
                        continue
                    if set(opening.rooms) != {before[2], after[2]}:
                        continue
                    gap = LineString(opening.gap_polyline_m)
                    crossing = segment.intersection(gap)
                    if crossing.geom_type != "Point" or not segment.crosses(gap):
                        continue
                    at = segment.project(crossing)
                    if before[1] - config.tolerance_m <= at <= after[0] + config.tolerance_m:
                        allowed.append(opening_id)
                assert len(allowed) == 1, f"No unique authorized opening for {before[2]} -> {after[2]} on {segment.wkt}"
                authorized_ids.extend(allowed)
            assert data["opening_ids"] == tuple(authorized_ids)
    return check
