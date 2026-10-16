"""Task 3.11: real-geometry retracing through a sole doorway."""

from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.routing import DistanceTable, RoutingCache, build_route_library
from othello.solver import (
    construct_f0_design, improve_design, optimize_partitions, select_whole_design_routes,
)
from othello.solver.tests._doubles import make_case, make_node, make_opening, make_room
from othello.solver.tests.test_f0 import partition, single_door_plan


def _config(*, hub_cap=6, order_cap=6, route_cap=2):
    return NS(
        hub_grid_step_m=0.5, hub_wall_clearance_m=0.5,
        tolerance_m=0.001, grid_step_m=0.5, cable_wall_margin_m=0.02,
        cable_transit_policy="forbid", time_budget_s=20.0,
        weights=NS(length=1.0, bend=0.5, crossing=5.0, overlap=1.0),
        limits=NS(hub_candidates=hub_cap, orders_kept=order_cap,
                  routes_per_leg=route_cap, beam_width=3,
                  exact_combo_limit=100, partitions_per_H=1),
    )


def _dead_end_chain(*, terminal_kind="door"):
    """R3 has only D23; its node can leave only by retracing that doorway."""
    rooms = [make_room("R1", box(0, 0, 4, 5)),
             make_room("R2", box(4, 0, 8, 5)),
             make_room("R3", box(8, 0, 12, 5))]
    walls = unary_union([
        box(3.9, 0, 4.1, 2), box(3.9, 3, 4.1, 5),
        box(7.9, 0, 8.1, 2), box(7.9, 3, 8.1, 5),
    ])
    zones = {room.room_id: room.polygon_m.difference(walls.buffer(0.5)) for room in rooms}
    nodes = [make_node("N1", "R1", (1.0, 2.5)),
             make_node("N2", "R2", (6.0, 2.5)),
             make_node("N3", "R3", (11.0, 2.5))]
    terminal_id = "P23" if terminal_kind == "open_passage" else "D23"
    openings = [make_opening("D12", [(4, 2), (4, 3)], rooms=("R1", "R2")),
                make_opening(terminal_id, [(8, 2), (8, 3)],
                             rooms=("R2", "R3"), kind=terminal_kind)]
    plan, territory = make_case(rooms=rooms, zones=zones, nodes=nodes,
                                openings=openings, walls=walls, inner=box(0, 0, 12, 5))
    plan.plan_id = "dead-end-chain"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    return plan, partition(territory)


def _crossed_openings(plan, polyline):
    crossed = []
    for start, goal in zip(polyline, polyline[1:]):
        if start == goal:
            continue
        segment = LineString([start, goal])
        encountered = []
        for opening_id in sorted(plan.openings):
            gap = LineString(plan.openings[opening_id].gap_polyline_m)
            if segment.crosses(gap):
                encountered.append((segment.project(segment.intersection(gap)), opening_id))
        crossed.extend(opening_id for _, opening_id in sorted(encountered))
    return crossed


def _assert_complete_geometry(plan, design):
    assert design["plan_id"] == plan.plan_id
    assert len(design["hubs"]) == 1
    hub = design["hubs"][0]
    assert hub["room_id"] in hub["assigned_rooms"]
    assert plan.rooms[hub["room_id"]].polygon_m.contains(Point(hub["location"]))
    assert Point(hub["location"]).distance(plan.walls_m) >= 0.5 - 1e-12
    ring = hub["ring"]
    sequence = ring["sequence"]
    assert sequence[0] == sequence[-1] == ring["hub_id"] == hub["hub_id"]
    assert sorted(sequence[1:-1]) == sorted(hub["assigned_nodes"])
    assert len(ring["legs"]) == len(sequence) - 1
    stops = {hub["hub_id"]: tuple(hub["location"])}
    stops.update({node_id: tuple(plan.nodes[node_id].point_m)
                  for node_id in hub["assigned_nodes"]})
    for (source, target), leg in zip(zip(sequence, sequence[1:]), ring["legs"]):
        assert (leg["from_id"], leg["to_id"]) == (source, target)
        assert tuple(leg["polyline"][0]) == stops[source]
        assert tuple(leg["polyline"][-1]) == stops[target]
        assert leg["doors_used"] == _crossed_openings(plan, leg["polyline"])
        for a, b in zip(leg["polyline"], leg["polyline"][1:]):
            if a == b:
                assert stops[source] == stops[target]
                continue
            assert (a[0] == b[0]) != (a[1] == b[1])
            segment = LineString([a, b])
            assert plan.inner_m.covers(segment)
            assert plan.free_space_m.covers(segment)
            assert segment.disjoint(plan.walls_m)
            assert segment.disjoint(plan.windows_m)


def _retraced_at_node(plan, design, node_id):
    ring = design["hubs"][0]["ring"]
    index = ring["sequence"].index(node_id)
    incoming = LineString(ring["legs"][index - 1]["polyline"])
    outgoing = LineString(ring["legs"][index]["polyline"])
    room = plan.rooms[plan.nodes[node_id].room_id].polygon_m
    return incoming.intersection(outgoing).intersection(room).length


def _travel_direction(a, b):
    if a[0] == b[0]:
        return "N" if b[1] > a[1] else "S"
    return "E" if b[0] > a[0] else "W"


def _checker(plan, design, config):
    """Geometry oracle for tests, not an implementation of official J."""
    _assert_complete_geometry(plan, design)
    used = [opening for hub in design["hubs"] for leg in hub["ring"]["legs"]
            for opening in leg["doors_used"]]
    assert used.count("D23") == 2
    # The mock accepts overlap. Part 2 will supply the actual O and B metrics.
    return NS(valid=True, metrics=NS(J=1.0))


def test_route_library_keeps_both_directions_through_only_door():
    plan, _ = single_door_plan()
    config = _config()
    table = DistanceTable(plan, {"H": (1.0, 2.5), "N": (7.0, 2.5)},
                          config, RoutingCache())
    outbound = build_route_library(table, "H", "N", config)
    returning = build_route_library(table, "N", "H", config, initial_direction="E")
    assert outbound.status == returning.status == "ready"
    assert outbound.routes and returning.routes
    assert outbound.routes[0].opening_ids == returning.routes[0].opening_ids == ("D1",)
    assert returning.routes[0].polyline == tuple(reversed(outbound.routes[0].polyline))
    assert returning.routes[0].first_direction == "W"
    assert returning.routes[0].initial_bend == 1  # 180-degree reversal counts once.
    for route in (*outbound.routes, *returning.routes):
        assert _crossed_openings(plan, route.polyline) == list(route.opening_ids)
    timed = build_route_library(table, "H", "N", config, deadline=0.0)
    assert timed.status == "timeout" and not timed.routes


def test_f0_multiroom_ring_retraces_dead_end_and_repeats_door_metadata():
    plan, option = _dead_end_chain()
    result = construct_f0_design(plan, option, _config())
    assert result.status == "constructed_unverified", result.diagnostics
    _assert_complete_geometry(plan, result.design)
    assert _retraced_at_node(plan, result.design, "N3") > 1.0
    doors = [opening for leg in result.design["hubs"][0]["ring"]["legs"]
             for opening in leg["doors_used"]]
    assert doors.count("D23") == 2
    assert doors.count("D12") >= 2
    assert result.design["hubs"][0]["ring"]["sequence"].count("N3") == 1
    ring = result.design["hubs"][0]["ring"]
    index = ring["sequence"].index("N3")
    incoming, outgoing = ring["legs"][index - 1]["polyline"], ring["legs"][index]["polyline"]
    assert _travel_direction(incoming[-2], incoming[-1]) == "E"
    assert _travel_direction(outgoing[0], outgoing[1]) == "W"


def test_open_passage_at_dead_end_is_retraced_like_a_door():
    plan, option = _dead_end_chain(terminal_kind="open_passage")
    result = construct_f0_design(plan, option, _config())
    assert result.status == "constructed_unverified", result.diagnostics
    _assert_complete_geometry(plan, result.design)
    assert _retraced_at_node(plan, result.design, "N3") > 1.0
    used = [opening for leg in result.design["hubs"][0]["ring"]["legs"]
            for opening in leg["doors_used"]]
    assert used.count("P23") == 2


def test_route_selection_and_improvement_keep_valid_dead_end_access():
    plan, option = _dead_end_chain()
    config = _config(hub_cap=4, order_cap=3)
    built = construct_f0_design(plan, option, config)
    assert built.status == "constructed_unverified"
    selected = select_whole_design_routes(plan, built.design, config, checker=_checker)
    assert selected.status == "verified" and selected.verified
    _assert_complete_geometry(plan, selected.design)
    assert selected.J <= _checker(plan, built.design, config).metrics.J

    improved = improve_design(plan, selected.design, config, checker=_checker)
    assert improved.status in {"unchanged", "improved"} and improved.verified
    _assert_complete_geometry(plan, improved.design)
    assert improved.J <= selected.J


def test_outer_search_accepts_retracing_as_a_verified_incumbent():
    plan, option = _dead_end_chain()
    result = optimize_partitions(plan, {1: ([option], "feasible")}, _config(hub_cap=4, order_cap=3),
                                 checker=_checker, lower_bound_hubs=1)
    assert result.verified and result.design is not None
    assert result.hub_count_status.tried == {1: "feasible"}
    _assert_complete_geometry(plan, result.design)


def test_bad_door_ownership_and_deadline_never_return_a_partial_ring():
    plan, option = _dead_end_chain()
    config = _config()
    expired = construct_f0_design(plan, option, config, deadline=0.0)
    assert expired.status == "timeout" and expired.design is None

    broken = deepcopy(plan)
    broken.openings["D23"].rooms = ["R1", "R2", "R3"]
    unsupported = construct_f0_design(broken, option, config)
    assert unsupported.status == "unsupported_geometry" and unsupported.design is None
    table = DistanceTable(broken, {"A": (6.0, 2.5), "B": (11.0, 2.5)}, config, RoutingCache())
    library = build_route_library(table, "A", "B", config)
    assert library.status == "unsupported_geometry" and not library.routes
