"""Contract and geometry checks for the unverified F0 assembly foundation."""

from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from shapely.geometry import GeometryCollection, LineString, Point, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.routing import RoutingCache
from othello.solver import construct_f0_design
from othello.solver.tests._doubles import make_case, make_config, make_node, make_opening, make_room


def config(**changes):
    values = dict(
        hub_grid_step_m=0.5, hub_wall_clearance_m=0.5, tolerance_m=0.001,
        grid_step_m=0.5, cable_wall_margin_m=0.02, cable_transit_policy="forbid",
        weights=SimpleNamespace(length=1.0, bend=0.5),
        limits=SimpleNamespace(hub_candidates=12, orders_kept=12),
    )
    values.update(changes)
    return make_config(**values)


def partition(*territories):
    for index, territory in enumerate(territories, start=1):
        territory.territory_id = f"T{index}"
    return SimpleNamespace(hub_count=len(territories), territories=list(territories), area_gap_m2=0)


def completed_plan(*, node_point=(1.0, 1.0)):
    plan, territory = make_case(nodes=[make_node("N1", "R1", node_point)])
    plan.plan_id = "F0-one-room"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    return plan, partition(territory)


def single_door_plan():
    rooms = [make_room("R1", box(0, 0, 4, 5)), make_room("R2", box(4, 0, 8, 5))]
    walls = unary_union([box(3.9, 0, 4.1, 2.0), box(3.9, 3.0, 4.1, 5.0)])
    zones = {room.room_id: room.polygon_m.difference(walls.buffer(0.5)) for room in rooms}
    nodes = [make_node("N1", "R1", (1.0, 2.5)), make_node("N2", "R2", (7.0, 2.5))]
    door = make_opening("D1", [(4.0, 2.0), (4.0, 3.0)], rooms=("R1", "R2"))
    plan, territory = make_case(rooms=rooms, zones=zones, nodes=nodes,
                                openings=[door], walls=walls, inner=box(0, 0, 8, 5))
    plan.plan_id = "F0-single-door"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    return plan, partition(territory)


def _assert_design_structure_and_geometry(design, plan):
    assert set(design) == {"plan_id", "hubs", "solver_notes"}
    assert design["plan_id"] == plan.plan_id
    assert design["solver_notes"]["verification"] == "not_run"
    for hub in design["hubs"]:
        assert set(hub) == {"hub_id", "room_id", "location", "assigned_rooms", "assigned_nodes", "ring"}
        assert plan.rooms[hub["room_id"]].polygon_m.contains(Point(hub["location"]))
        ring = hub["ring"]
        sequence = ring["sequence"]
        assert ring["hub_id"] == hub["hub_id"]
        assert sequence[0] == sequence[-1] == hub["hub_id"]
        assert len(ring["legs"]) == len(sequence) - 1
        assert sorted(sequence[1:-1]) == sorted(hub["assigned_nodes"])
        stops = {hub["hub_id"]: hub["location"]}
        stops.update({node_id: plan.nodes[node_id].point_m for node_id in hub["assigned_nodes"]})
        for (source, target), leg in zip(zip(sequence, sequence[1:]), ring["legs"]):
            assert set(leg) == {"from_id", "to_id", "polyline", "doors_used"}
            assert (leg["from_id"], leg["to_id"]) == (source, target)
            assert len(leg["polyline"]) >= 2
            assert tuple(leg["polyline"][0]) == stops[source]
            assert tuple(leg["polyline"][-1]) == stops[target]
            crossed = []
            for a, b in zip(leg["polyline"], leg["polyline"][1:]):
                if a == b:
                    assert source != target and stops[source] == stops[target]
                    continue
                assert (a[0] == b[0]) != (a[1] == b[1])
                segment = LineString([a, b])
                assert plan.inner_m.covers(segment)
                assert plan.free_space_m.covers(segment)
                assert segment.disjoint(plan.walls_m)
                for opening_id, opening in plan.openings.items():
                    if segment.crosses(LineString(opening.gap_polyline_m)):
                        crossed.append(opening_id)
            assert leg["doors_used"] == crossed


def test_one_room_closed_ring_keeps_coincident_visit_and_adapter_fields():
    plan, option = completed_plan()
    result = construct_f0_design(plan, option, config())
    assert result.status == "constructed_unverified", result.diagnostics
    _assert_design_structure_and_geometry(result.design, plan)
    ring = result.design["hubs"][0]["ring"]
    assert ring["sequence"] == ["H1", "N1", "H1"]
    assert result.design["solver_notes"]["coincident_legs_encoded_as_duplicate_points"] == 2
    assert all(leg["polyline"] == [(1.0, 1.0), (1.0, 1.0)] for leg in ring["legs"])


def test_single_door_retrace_keeps_both_crossings_and_all_node_visits():
    plan, option = single_door_plan()
    result = construct_f0_design(plan, option, config())
    assert result.status == "constructed_unverified", result.diagnostics
    _assert_design_structure_and_geometry(result.design, plan)
    hub = result.design["hubs"][0]
    ring = hub["ring"]
    assert sorted(ring["sequence"][1:-1]) == ["N1", "N2"]
    assert sum(leg["doors_used"].count("D1") for leg in ring["legs"]) == 2
    assert ring["sequence"][-1] == hub["hub_id"]
    assert ring["legs"][2]["polyline"] == list(reversed(ring["legs"][1]["polyline"]))


def test_two_territories_form_two_complete_rings_and_failure_is_atomic():
    rooms = [make_room("R1", box(0, 0, 4, 4)), make_room("R2", box(4, 0, 8, 4))]
    nodes = [make_node("N1", "R1", (1.0, 1.0)), make_node("N2", "R2", (7.0, 1.0))]
    passage = make_opening("P1", [(4.0, 0.0), (4.0, 4.0)],
                           rooms=("R1", "R2"), kind="open_passage")
    plan, _ = make_case(rooms=rooms, nodes=nodes, openings=[passage], inner=box(0, 0, 8, 4))
    plan.plan_id = "F0-two-hubs"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    first = SimpleNamespace(room_ids=["R1"], node_ids=["N1"], hub_zone_m=plan.hub_zones_m["R1"])
    second = SimpleNamespace(room_ids=["R2"], node_ids=["N2"], hub_zone_m=plan.hub_zones_m["R2"])
    option = partition(first, second)
    result = construct_f0_design(plan, option, config())
    assert result.status == "constructed_unverified", result.diagnostics
    assert len(result.design["hubs"]) == 2
    _assert_design_structure_and_geometry(result.design, plan)
    plan.hub_zones_m["R2"] = GeometryCollection()
    second.hub_zone_m = GeometryCollection()
    failed = construct_f0_design(plan, option, config())
    assert failed.status == "no_candidates" and failed.design is None


def test_mandatory_hub_coverage_above_cap_is_structured_failure():
    plan, option = completed_plan()
    plan.openings["LEFT"] = make_opening("LEFT", [(0, 1.5), (0, 2.5)])
    plan.openings["RIGHT"] = make_opening("RIGHT", [(4, 1.5), (4, 2.5)])
    limited = config(limits=SimpleNamespace(hub_candidates=1, orders_kept=12))
    result = construct_f0_design(plan, option, limited)
    assert result.status == "candidate_configuration_error"
    assert result.design is None
    assert "limits.hub_candidates=1" in result.diagnostics[0]
    assert "distinct required representatives" in result.diagnostics[0]


def test_deterministic_construction_and_cache_reuse():
    plan, option = single_door_plan()
    cache = RoutingCache()
    first = construct_f0_design(plan, option, config(), cache)
    hits = cache.stats().path_hits
    second = construct_f0_design(plan, option, config(), cache)
    assert first == second
    assert cache.stats().path_hits > hits
    assert first == construct_f0_design(plan, option, config(), RoutingCache())


def test_f0_reuses_one_hanan_graph_for_all_order_legs(monkeypatch):
    from othello.routing import table as table_module
    from othello.solver import f0 as f0_module
    from othello.solver.types import HubCandidate, HubCandidateResult

    nodes = [make_node("N1", "R1", (1.0, 1.0)), make_node("N2", "R1", (3.0, 3.0))]
    plan, territory = make_case(nodes=nodes)
    plan.plan_id = "F0-shared-graph"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    option = partition(territory)
    candidate = HubCandidate((2.0, 2.0), "R1", ("grid",), (), 4.0)
    monkeypatch.setattr(f0_module, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("ready", (candidate,)))
    original = table_module.build_routing_graph
    builds = []

    def count(*args, **kwargs):
        builds.append((args[2], args[3]))
        return original(*args, **kwargs)

    monkeypatch.setattr(table_module, "build_routing_graph", count)
    cache = RoutingCache()
    first = construct_f0_design(plan, option, config(), cache)
    assert first.status == "constructed_unverified"
    _assert_design_structure_and_geometry(first.design, plan)
    assert len(builds) == 1
    assert cache.stats().graph_hits >= 1
    assert first == construct_f0_design(plan, option, config(), cache)
    assert len(builds) == 1
    assert first == construct_f0_design(plan, option, config(), RoutingCache())


def test_mid_construction_timeout_discards_completed_first_ring(monkeypatch):
    from othello.solver import f0
    rooms = [make_room("R1", box(0, 0, 4, 4)), make_room("R2", box(4, 0, 8, 4))]
    nodes = [make_node("N1", "R1", (1, 1)), make_node("N2", "R2", (7, 1))]
    passage = make_opening("P1", [(4, 0), (4, 4)], rooms=("R1", "R2"), kind="open_passage")
    plan, _ = make_case(rooms=rooms, nodes=nodes, openings=[passage], inner=box(0, 0, 8, 4))
    plan.plan_id = "timeout-after-first-hub"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    first = SimpleNamespace(room_ids=["R1"], node_ids=["N1"], hub_zone_m=plan.hub_zones_m["R1"])
    second = SimpleNamespace(room_ids=["R2"], node_ids=["N2"], hub_zone_m=plan.hub_zones_m["R2"])
    option = partition(first, second)
    reached_second = False
    original = f0.generate_hub_candidates

    def track_second(*args, **kwargs):
        nonlocal reached_second
        if args[1] is second:
            reached_second = True
        return original(*args, **kwargs)

    monkeypatch.setattr(f0, "generate_hub_candidates", track_second)
    monkeypatch.setattr(f0, "_expired", lambda deadline: reached_second)
    result = construct_f0_design(plan, option, config(), deadline=10**20)
    assert reached_second
    assert result.status == "timeout" and result.design is None


def test_concave_room_detour_stays_in_true_free_space():
    room = box(0, 0, 8, 6).difference(box(2, 2, 6, 6))
    zone = box(0.5, 4, 1, 4.5)
    plan, territory = make_case(rooms=[make_room("R1", room)], zones={"R1": zone},
                                nodes=[make_node("N1", "R1", (7, 4))], inner=room)
    plan.plan_id = "concave-detour"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    result = construct_f0_design(plan, partition(territory), config())
    assert result.status == "constructed_unverified", result.diagnostics
    _assert_design_structure_and_geometry(result.design, plan)
    leg = result.design["hubs"][0]["ring"]["legs"][0]
    assert min(y for _, y in leg["polyline"]) < 2


def test_window_obstacle_is_never_crossed():
    window = box(2, 2, 4, 3)
    room = make_room("R1", box(0, 0, 6, 5))
    plan, territory = make_case(rooms=[room], zones={"R1": box(0.6, 2.2, 1.1, 2.8)},
                                nodes=[make_node("N1", "R1", (5, 2.5))],
                                walls=window, inner=room.polygon_m)
    plan.plan_id = "window-detour"
    plan.windows_m = window
    plan.excluded_interior_rooms = []
    result = construct_f0_design(plan, partition(territory), config())
    assert result.status == "constructed_unverified", result.diagnostics
    _assert_design_structure_and_geometry(result.design, plan)
    for leg in result.design["hubs"][0]["ring"]["legs"]:
        for a, b in zip(leg["polyline"], leg["polyline"][1:]):
            if a != b:
                assert LineString([a, b]).disjoint(plan.windows_m)


def excluded_detour_plan():
    rooms = [make_room("R1", box(0, 0, 3, 6)),
             make_room("X", box(3, 0, 7, 6), eligible=False),
             make_room("R2", box(7, 0, 10, 6)),
             make_room("R3", box(0, 6, 10, 10))]
    wall = box(3, 5.9, 7, 6.1)
    zones = {room.room_id: room.polygon_m.difference(wall.buffer(0.5)) for room in rooms}
    openings = [make_opening("D1", [(3, 1), (3, 3)], rooms=("R1", "X")),
                make_opening("D2", [(7, 1), (7, 3)], rooms=("X", "R2")),
                make_opening("P1", [(1, 6), (2, 6)], rooms=("R1", "R3"), kind="open_passage"),
                make_opening("P2", [(8, 6), (9, 6)], rooms=("R2", "R3"), kind="open_passage")]
    nodes = [make_node("N1", "R1", (1, 2)), make_node("N2", "R2", (9, 2)),
             make_node("N3", "R3", (5, 8))]
    plan, territory = make_case(rooms=rooms, zones=zones, nodes=nodes, openings=openings,
                                walls=wall, inner=box(0, 0, 10, 10),
                                room_ids=["R1", "R2", "R3"], node_ids=["N1", "N2", "N3"])
    plan.plan_id = "excluded-detour"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = ["X"]
    return plan, partition(territory)


def test_excluded_room_policy_uses_permitted_detour_first():
    plan, option = excluded_detour_plan()
    for policy, expected_doors, excluded_length in (
        ("forbid", ["P1", "P2"], 0),
        ("avoid_if_possible", ["P1", "P2"], 0),
        ("allow", ["D1", "D2"], 4),
    ):
        result = construct_f0_design(plan, option, config(cable_transit_policy=policy))
        assert result.status == "constructed_unverified", (policy, result.diagnostics)
        _assert_design_structure_and_geometry(result.design, plan)
        legs = result.design["hubs"][0]["ring"]["legs"]
        assert legs[1]["doors_used"] == expected_doors
        travelled = sum(LineString([a, b]).intersection(plan.rooms["X"].polygon_m).length
                        for leg in legs for a, b in zip(leg["polyline"], leg["polyline"][1:]) if a != b)
        assert travelled == pytest.approx(excluded_length)


def test_no_candidates_and_bad_geometry_return_no_partial_design():
    plan, option = completed_plan()
    plan.hub_zones_m["R1"] = GeometryCollection()
    option.territories[0].hub_zone_m = GeometryCollection()
    absent = construct_f0_design(plan, option, config())
    assert absent.status == "no_candidates" and absent.design is None
    option.territories[0].hub_zone_m = box(0, 0, 1, 1)
    unsupported = construct_f0_design(plan, option, config())
    assert unsupported.status == "unsupported_geometry" and unsupported.design is None


def test_expired_deadline_returns_no_design():
    plan, option = completed_plan()
    result = construct_f0_design(plan, option, config(), deadline=0.0)
    assert result.status == "timeout" and result.design is None


def test_partition_contract_rejects_duplicate_node_assignment():
    plan, option = completed_plan()
    duplicate = SimpleNamespace(territory_id="T2", room_ids=["R1"], node_ids=["N1"],
                                hub_zone_m=option.territories[0].hub_zone_m)
    with pytest.raises(ValueError, match="unique across territories"):
        construct_f0_design(plan, SimpleNamespace(hub_count=2,
                             territories=[option.territories[0], duplicate]), config())
