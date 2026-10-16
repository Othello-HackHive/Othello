"""Task 3.6: geometric alternatives on the selected leg graph."""

from types import SimpleNamespace

import pytest
from shapely.geometry import GeometryCollection, LineString, Polygon, box
from shapely.ops import unary_union

from conftest import make_config, make_opening, make_plan, make_room
from othello.routing import DistanceTable, RoutingCache, build_route_library
from othello.models import SolverConfig
from test_astar import path_graph, tradeoff_graph
from test_transit_policy import transit_plan


def config(*, cap=6, length=1.0, bend=0.5, policy="forbid"):
    return make_config(grid_step_m=0.5, cable_transit_policy=policy,
                       weights=SimpleNamespace(length=length, bend=bend),
                       limits=SimpleNamespace(routes_per_leg=cap))


def table_for(plan, stops, cfg):
    return DistanceTable(plan, stops, cfg, RoutingCache())


def test_occupied_cable_variant_is_distinct_and_does_not_poison_route_cache():
    plan = make_plan(inner=box(0, 0, 6, 6))
    cfg = SolverConfig()
    cache = RoutingCache()
    table = DistanceTable(plan, {"A": (1.0, 1.0), "B": (5.0, 5.0)}, cfg, cache)
    baseline = table.get_route("A", "B")
    assert baseline.status == "success"
    library = build_route_library(table, "A", "B", cfg,
                                  occupied_polylines=(tuple(baseline.polyline),))
    assert library.status == "ready"
    alternatives = [route for route in library.routes if "avoid_placed_cable" in route.source_tags]
    assert alternatives
    assert any(route.polyline != tuple(baseline.polyline) for route in alternatives)
    assert table.get_route("A", "B").polyline == baseline.polyline


def two_doors():
    walls = unary_union([box(4, 0, 6, 3.5), box(4, 4.5, 6, 6.5), box(4, 7.5, 6, 10)])
    return make_plan(
        inner=box(0, 0, 10, 10), walls=walls,
        rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
        openings=[make_opening("D1", ["R1", "R2"], [(5, 3.5), (5, 4.5)]),
                  make_opening("D2", ["R1", "R2"], [(5, 6.5), (5, 7.5)])],
    )


def test_real_geometry_alternative_door_and_independent_segment_checks():
    plan = two_doors()
    cfg = config()
    stops = {"H": (1.0, 4.0), "N": (9.0, 4.0)}
    result = build_route_library(table_for(plan, stops, cfg), "H", "N", cfg)
    assert result.status == "ready", result.diagnostics
    assert 1 < len(result.routes) <= cfg.limits.routes_per_leg
    assert result.routes[0].opening_ids == ("D1",)
    assert any(route.opening_ids == ("D2",) for route in result.routes)
    assert len({route.polyline for route in result.routes}) == len(result.routes)
    for route in result.routes:
        assert route.polyline[0] == stops["H"] and route.polyline[-1] == stops["N"]
        crossed = []
        for a, b in zip(route.polyline, route.polyline[1:]):
            assert (a[0] == b[0]) != (a[1] == b[1])
            segment = LineString([a, b])
            assert plan.inner_m.covers(segment)
            assert plan.free_space_m.covers(segment)
            assert segment.disjoint(plan.walls_m)
            for opening_id, opening in plan.openings.items():
                if segment.crosses(LineString(opening.gap_polyline_m)):
                    crossed.append(opening_id)
        assert route.opening_ids == tuple(crossed)


def test_shortest_length_and_fewest_bends_tradeoff(monkeypatch):
    plan = make_plan()
    cfg = config(bend=2.0)
    stops = {"H": (0, 0), "N": (4, 2)}
    monkeypatch.setattr("othello.routing.table.build_routing_graph",
                        lambda *args, **kwargs: SimpleNamespace(status="ready", graph=tradeoff_graph()))
    result = build_route_library(table_for(plan, stops, cfg), "H", "N", cfg)
    assert result.status == "ready"
    assert {route.length_m for route in result.routes} == {6.0, 8.0}
    long = next(route for route in result.routes if route.length_m == 8)
    short = next(route for route in result.routes if route.length_m == 6)
    assert long.internal_bends == 2 and short.internal_bends == 4
    assert "normal_weighted" in long.source_tags and "fewest_bends" in long.source_tags
    assert "shortest_length" in short.source_tags
    assert long.weighted_leg_cost == 12 and short.weighted_leg_cost == 14


def test_equivalent_geometry_deduplicates_and_merges_variant_tags():
    plan = make_plan()
    cfg = config()
    result = build_route_library(table_for(plan, {"H": (1, 1), "N": (9, 1)}, cfg), "H", "N", cfg)
    assert result.status == "ready"
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.polyline == ((1.0, 1.0), (9.0, 1.0))
    assert route.source_tags == ("normal_weighted", "shortest_length", "fewest_bends")
    assert route.opening_ids == ()


def test_exact_float_endpoints_and_repeated_opening_order(monkeypatch):
    start, goal = (0.123456789, 0.25), (1.25, 1.125)
    points = [start, (1.25, 0.25), (2.75, 0.25), (2.75, 1.125), goal]
    graph = path_graph(points, [("D1",), ("P1",), ("D1",), ("P2", "D2")])
    plan = make_plan()
    cfg = config()
    monkeypatch.setattr("othello.routing.table.build_routing_graph",
                        lambda *args, **kwargs: SimpleNamespace(status="ready", graph=graph))
    result = build_route_library(table_for(plan, {"H": start, "N": goal}, cfg), "H", "N", cfg)
    assert result.status == "ready"
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.polyline[0] == start and route.polyline[-1] == goal
    assert route.opening_ids == ("D1", "P1", "D1", "P2", "D2")
    assert route.polyline == (start, (2.75, 0.25), (2.75, 1.125), goal)


def test_geometry_signature_ignores_splits_but_keeps_reversals():
    from othello.routing.route_library import _signature
    start, goal = (0.0, 0.0), (1.0, 0.0)
    assert _signature([start, (0.5, 0.0), (2.0, 0.0), goal], start, goal) == (
        start, (2.0, 0.0), goal,
    )


def test_cap_one_retains_normal_weighted_baseline():
    plan = two_doors()
    cfg = config(cap=1)
    result = build_route_library(table_for(plan, {"H": (1, 4), "N": (9, 4)}, cfg), "H", "N", cfg)
    assert result.status == "ready"
    assert len(result.routes) == 1
    assert result.routes[0].opening_ids == ("D1",)


def test_coincident_endpoints_keep_one_point_internal_route():
    plan = make_plan()
    cfg = config()
    result = build_route_library(table_for(plan, {"H": (2.0, 2.0), "N": (2.0, 2.0)}, cfg),
                                 "H", "N", cfg, initial_direction="N")
    assert result.status == "ready"
    assert len(result.routes) == 1
    route = result.routes[0]
    assert route.polyline == ((2.0, 2.0),)
    assert route.length_m == route.weighted_leg_cost == 0
    assert route.first_direction is route.last_direction is None


def test_excluded_room_policy_is_never_relaxed_by_variants():
    plan = make_plan(
        inner=box(0, 0, 9, 6),
        rooms=[make_room("R1", box(0, 0, 3, 6)), make_room("R2", box(3, 0, 6, 6), excluded=True),
               make_room("R3", box(6, 0, 9, 6))],
        openings=[make_opening("D1", ["R1", "R2"], [(3, 2), (3, 4)]),
                  make_opening("D2", ["R2", "R3"], [(6, 2), (6, 4)])],
        excluded=("R2",),
    )
    cfg = config(policy="avoid_if_possible")
    stops = {"H": (1.0, 2.0), "N": (2.0, 4.0)}
    result = build_route_library(table_for(plan, stops, cfg), "H", "N", cfg)
    assert result.status == "ready", result.diagnostics
    assert all(all(x < 3 for x, _ in route.polyline) for route in result.routes)
    assert all(not route.opening_ids for route in result.routes)


def test_policy_specific_libraries_do_not_share_relaxed_graph():
    plan = transit_plan()
    stops = {"H": (1, 2), "N": (9, 2)}
    cache = RoutingCache()
    forbid_config = config(cap=2, policy="forbid")
    allow_config = config(cap=2, policy="allow")
    forbidden = build_route_library(DistanceTable(plan, stops, forbid_config, cache),
                                    "H", "N", forbid_config)
    allowed = build_route_library(DistanceTable(plan, stops, allow_config, cache),
                                  "H", "N", allow_config)
    forbidden_again = build_route_library(DistanceTable(plan, stops, forbid_config, cache),
                                          "H", "N", forbid_config)
    assert forbidden.status == forbidden_again.status == "unreachable_on_grid"
    assert not forbidden.routes and not forbidden_again.routes
    assert allowed.status == "ready", allowed.diagnostics
    assert allowed.routes[0].opening_ids == ("D1", "D2")


def test_graph_and_path_cache_reused_across_library_calls():
    plan = make_plan()
    cfg = config()
    table = table_for(plan, {"H": (1, 1), "N": (9, 1)}, cfg)
    first = build_route_library(table, "H", "N", cfg)
    stats_first = table.cache.stats()
    second = build_route_library(table, "H", "N", cfg)
    stats_second = table.cache.stats()
    assert first == second
    assert stats_first.path_count == stats_second.path_count == 1
    assert stats_first.graph_count == stats_second.graph_count
    assert stats_first.graph_count >= 1
    assert stats_second.path_hits > stats_first.path_hits
    assert stats_second.graph_hits > stats_first.graph_hits


def test_library_reuses_shared_graph_across_legs_and_matches_fresh(monkeypatch):
    from othello.routing import table as table_module

    plan = two_doors()
    cfg = config()
    stops = {"A": (1.0, 4.0), "B": (9.0, 4.0), "C": (1.0, 7.0)}
    original = table_module.build_routing_graph
    builds = []

    def count(*args, **kwargs):
        builds.append((args[2], args[3]))
        return original(*args, **kwargs)

    monkeypatch.setattr(table_module, "build_routing_graph", count)
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)
    first = build_route_library(table, "A", "B", cfg)
    second = build_route_library(table, "C", "B", cfg)
    assert first.status == second.status == "ready"
    assert builds == [("A", "B")]
    assert {key.leg for key in cache._graphs} == {None}
    assert second == build_route_library(table_for(plan, stops, cfg), "C", "B", cfg)
    assert first == build_route_library(table_for(plan, stops, cfg), "A", "B", cfg)


@pytest.mark.parametrize("fallback_first", [False, True])
def test_library_preserves_pair_fallback_and_hanan_variants(fallback_first):
    inner = Polygon([(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)])
    plan = make_plan(inner=inner)
    stops = {"A": (1.123, 1.321), "B": (1.523, 1.321), "C": (5.321, 1.321)}
    cfg = config(policy="forbid")
    table = table_for(plan, stops, cfg)
    targets = ("C", "B") if fallback_first else ("B", "C")
    for target in targets:
        warm = build_route_library(table, "A", target, cfg)
        fresh = build_route_library(table_for(plan, stops, cfg), "A", target, cfg)
        assert warm == fresh
        assert warm.status == "ready"
    assert {key.leg for key in table.cache._graphs} == {None, ("A", "C")}


def test_expired_deadline_returns_timeout_without_routes():
    plan = make_plan()
    cfg = config()
    table = table_for(plan, {"H": (1, 1), "N": (9, 1)}, cfg)
    result = build_route_library(table, "H", "N", cfg, deadline=0.0)
    assert result.status == "timeout"
    assert not result.routes


def test_variant_timeout_discards_baseline_and_partial_library(monkeypatch):
    from othello.routing import route_library
    from othello.routing.types import RouteSearchResult
    plan = make_plan()
    cfg = config()
    table = table_for(plan, {"H": (1, 1), "N": (9, 1)}, cfg)
    monkeypatch.setattr(route_library, "astar_route", lambda *args, **kwargs: RouteSearchResult("timeout"))
    result = build_route_library(table, "H", "N", cfg)
    assert result.status == "timeout"
    assert not result.routes


def test_same_geometry_with_conflicting_opening_metadata_is_diagnostic(monkeypatch):
    from othello.routing import route_library
    from othello.routing.types import RouteSearchResult
    plan = make_plan()
    cfg = config()
    table = table_for(plan, {"H": (1, 1), "N": (9, 1)}, cfg)
    contradictory = RouteSearchResult("success", [(1.0, 1.0), (9.0, 1.0)],
                                      8.0, 8.0, 0, 0, ("D1",), "E", "E")
    monkeypatch.setattr(route_library, "astar_route", lambda *args, **kwargs: contradictory)
    result = build_route_library(table, "H", "N", cfg)
    assert result.status == "unsupported_geometry"
    assert not result.routes
    assert "conflicting opening metadata" in result.diagnostics[0]


def test_policy_config_mismatch_is_rejected():
    plan = make_plan()
    cfg = config(policy="forbid")
    table = table_for(plan, {"H": (1, 1), "N": (9, 1)}, cfg)
    with pytest.raises(ValueError, match="match"):
        build_route_library(table, "H", "N", config(policy="allow"))
