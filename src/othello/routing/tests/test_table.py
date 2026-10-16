"""Tests for Task 3.3 DistanceTable, lazy evaluation, and status fidelity."""

from types import SimpleNamespace

import pytest
from shapely.geometry import Polygon, box

from conftest import make_config, make_opening, make_plan, make_room
from othello.routing import (
    DistanceTable,
    RoutingCache,
    RoutingFailureError,
    TableRouteResult,
    build_distance_table,
    build_routing_graph,
)


def sample_plan():
    return make_plan(
        inner=box(0, 0, 10, 10),
        walls=box(4, 0, 6, 8),
        rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
        openings=[make_opening("D1", ["R1", "R2"], [(5, 8.5), (5, 9.5)])],
    )


def test_distance_table_strictly_lazy_population():
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2), "N2": (1, 8)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()

    table = build_distance_table(plan, stops, cfg, cache)
    # Zero queries executed in constructor
    assert len(table._routes) == 0
    assert cache.stats().path_misses == 0
    assert cache.stats().graph_misses == 0

    # Query one leg
    route = table.get_route("H1", "N1")
    assert route.status == "success"
    assert len(table._routes) == 1
    assert cache.stats().path_misses == 1
    assert cache.stats().path_hits == 0

    # Query same leg again -> hit
    route2 = table.get_route("H1", "N1")
    assert route2.status == "success"
    assert route2.polyline == route.polyline
    assert cache.stats().path_hits == 1


def test_strict_winner_length_vs_cost_distinction(monkeypatch):
    # Route 'long':  (0,0) -> (0,3) -> (4,3) -> (4,2). Length = 8.0, Bends = 2.
    # Route 'short': (0,0) -> (1,0) -> (1,1) -> (2,1) -> (2,2) -> (4,2). Length = 6.0, Bends = 4.
    from test_astar import tradeoff_graph
    tg = tradeoff_graph()

    plan = sample_plan()
    stops = {"A": (0, 0), "B": (4, 2)}
    monkeypatch.setattr(
        "othello.routing.table.build_routing_graph",
        lambda *args, **kwargs: SimpleNamespace(status="ready", graph=tg, diagnostics=()),
    )

    # 1. Weights favor fewer bends (w_L=1.0, w_B=2.0)
    # Cost(long) = 8.0 * 1.0 + 2 * 2.0 = 12.0  (STRICT WINNER)
    # Cost(short) = 6.0 * 1.0 + 4 * 2.0 = 14.0
    cache1 = RoutingCache()
    cfg_bends = make_config(weights=SimpleNamespace(length=1.0, bend=2.0))
    table_bends = DistanceTable(plan, stops, cfg_bends, cache1)

    assert table_bends.get_distance("A", "B") == 8.0
    assert table_bends.get_cost("A", "B") == 12.0
    r_bends = table_bends.get_route("A", "B")
    assert r_bends.length_m == 8.0
    assert r_bends.internal_bends == 2
    assert r_bends.cost == 12.0

    # 2. Weights favor shorter length (w_L=1.0, w_B=0.1)
    # Cost(short) = 6.0 * 1.0 + 4 * 0.1 = 6.4   (STRICT WINNER)
    # Cost(long) = 8.0 * 1.0 + 2 * 0.1 = 8.2
    cache2 = RoutingCache()
    cfg_len = make_config(weights=SimpleNamespace(length=1.0, bend=0.1))
    table_len = DistanceTable(plan, stops, cfg_len, cache2)

    assert table_len.get_distance("A", "B") == 6.0
    assert table_len.get_cost("A", "B") == pytest.approx(6.4)
    r_len = table_len.get_route("A", "B")
    assert r_len.length_m == 6.0
    assert r_len.internal_bends == 4
    assert r_len.cost == pytest.approx(6.4)


def test_coincident_stops_preserves_node_visitation():
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (1, 2)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)

    # Coincident self-query
    assert table.get_distance("H1", "H1") == 0.0
    assert table.get_cost("H1", "H1") == 0.0
    r_self = table.get_route("H1", "H1")
    assert r_self.status == "success"
    assert r_self.polyline == [(1, 2)]
    assert r_self.length_m == 0.0
    assert r_self.cost == 0.0
    assert r_self.first_direction is None and r_self.last_direction is None

    # Coincident distinct terminal query
    assert table.get_distance("H1", "N1") == 0.0
    assert table.get_cost("H1", "N1") == 0.0
    r_pair = table.get_route("H1", "N1")
    assert r_pair.status == "success"
    assert r_pair.polyline == [(1, 2)]


@pytest.mark.parametrize("policy", ["forbid", "allow", "avoid_if_possible"])
@pytest.mark.parametrize("fallback_first", [False, True])
def test_graph_cache_respects_pair_dependent_fallback_for_every_policy(policy, fallback_first):
    # A concave plan separates the lower arms. The first leg stays in the
    # left arm on the Hanan lines; the second needs fallback y=5 to detour.
    inner = Polygon([(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)])
    plan = make_plan(inner=inner)
    stops = {"A": (1.123, 1.321), "B": (1.523, 1.321), "C": (5.321, 1.321)}
    cfg = make_config(grid_step_m=1.0, cable_transit_policy=policy,
                      weights=SimpleNamespace(length=1.0, bend=0.5))
    first_graph = build_routing_graph(plan, stops, "A", "B", cfg)
    second_graph = build_routing_graph(plan, stops, "A", "C", cfg)
    assert first_graph.status == second_graph.status == "ready"
    assert not first_graph.fallback_used
    assert second_graph.fallback_used

    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)
    order = ("C", "B") if fallback_first else ("B", "C")
    for target in order:
        cached = table.get_route("A", target)
        clean = DistanceTable(plan, stops, cfg, RoutingCache()).get_route("A", target)
        assert cached == clean
        assert cached.status == "success"
    assert {key.leg for key in cache._graphs} == {None, ("A", "C")}
    assert cache.stats().graph_count == 2
    if fallback_first:
        assert cache.stats().graph_hits == 0
    else:
        assert cache.stats().graph_hits >= 1  # Shared Hanan graph was checked before fallback.


def test_shared_hanan_graph_reuses_one_build_across_distinct_legs(monkeypatch):
    from othello.routing import table as table_module

    plan = make_plan(inner=box(0, 0, 10, 10))
    stops = {"A": (1, 1), "B": (3, 2), "C": (6, 4), "D": (8, 7)}
    cfg = make_config(cable_transit_policy="avoid_if_possible",
                      weights=SimpleNamespace(length=1.0, bend=0.5))
    original = table_module.build_routing_graph
    builds = []

    def count(*args, **kwargs):
        builds.append((args[2], args[3]))
        return original(*args, **kwargs)

    monkeypatch.setattr(table_module, "build_routing_graph", count)
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)
    legs = [("A", "B"), ("B", "C"), ("C", "D"), ("D", "A")]
    for source, target in legs:
        route = table.get_route(source, target)
        assert route == DistanceTable(plan, stops, cfg, RoutingCache()).get_route(source, target)
    # Fresh comparisons also use the spy, so only one of the warm table's four
    # queries should have required a build.
    assert builds == [legs[0], *legs]
    assert {key.leg for key in cache._graphs} == {None}
    assert cache.stats().graph_hits >= len(legs) - 1


def test_avoid_policy_does_not_share_relaxed_graph_with_permitted_detour():
    from conftest import make_opening

    rooms = [make_room("R1", box(0, 0, 3, 6)),
             make_room("X", box(3, 0, 7, 6), excluded=True),
             make_room("R2", box(7, 0, 10, 6)),
             make_room("R3", box(0, 6, 10, 10)),
             make_room("R4", box(3, -3, 7, 0))]
    openings = [make_opening("D1", ["R1", "X"], [(3, 1), (3, 3)]),
                make_opening("D2", ["X", "R2"], [(7, 1), (7, 3)]),
                make_opening("D3", ["X", "R4"], [(4, 0), (6, 0)]),
                make_opening("P1", ["R1", "R3"], [(1, 6), (2, 6)], "open_passage"),
                make_opening("P2", ["R2", "R3"], [(8, 6), (9, 6)], "open_passage")]
    plan = make_plan(inner=box(0, -3, 10, 10), rooms=rooms,
                     openings=openings, excluded=["X"])
    stops = {"A": (1, 2), "B": (9, 2), "C": (5, -2), "D": (2, 2)}
    cfg = make_config(grid_step_m=1.0, cable_transit_policy="avoid_if_possible",
                      weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)
    relaxed = table.get_route("A", "C")
    assert relaxed.status == "success" and relaxed.opening_ids == ("D1", "D3")
    assert None not in {key.leg for key in cache._graphs}
    permitted = table.get_route("A", "B")
    assert permitted == DistanceTable(plan, stops, cfg, RoutingCache()).get_route("A", "B")
    assert permitted.status == "success" and permitted.opening_ids == ("P1", "P2")
    assert permitted.length_m > relaxed.length_m
    assert None not in {key.leg for key in cache._graphs}  # This permitted leg used fallback.
    local = table.get_route("A", "D")
    assert local.status == "success" and local.opening_ids == ()
    assert None in {key.leg for key in cache._graphs}
    assert table.get_route("A", "B") == permitted
    # In the opposite order, the shared restricted Hanan graph is insufficient;
    # the leg must complete restricted fallback before considering X.
    cache2 = RoutingCache()
    table2 = DistanceTable(plan, stops, cfg, cache2)
    assert table2.get_route("A", "D") == local
    assert table2.get_route("A", "B") == permitted
    assert {key.leg for key in cache2._graphs} == {None, ("A", "B")}


@pytest.mark.parametrize("obstacle", ["walls", "windows"])
def test_warm_shared_graph_does_not_validate_blocked_coincident_terminal(obstacle):
    plan = make_plan(inner=box(0, 0, 10, 10), **{obstacle: box(4, 4, 6, 6)})
    stops = {"A": (1, 1), "B": (2, 1), "X": (5, 5), "Y": (5, 5)}
    cfg = make_config(cable_transit_policy="forbid",
                      weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)
    assert table.get_route("A", "B").status == "success"
    assert None in {key.leg for key in cache._graphs}
    for source, target in (("X", "X"), ("X", "Y")):
        result = table.get_route(source, target)
        assert result.status == "unreachable_on_grid"
        assert result == DistanceTable(plan, stops, cfg, RoutingCache()).get_route(source, target)


def test_shared_connectivity_timeout_does_not_cache_a_negative_route(monkeypatch):
    from othello.routing import table as table_module

    plan = make_plan(inner=box(0, 0, 10, 10))
    stops = {"A": (1, 1), "B": (2, 1), "C": (9, 9)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    clock = {"now": 0.0}
    monkeypatch.setattr(table_module.time, "monotonic", lambda: clock["now"])
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache, deadline=10.0)
    assert table.get_route("A", "B").status == "success"
    original = table_module._shared_reaches

    def expire_during_reachability(graph, start, goal, deadline):
        clock["now"] = deadline
        return original(graph, start, goal, deadline)

    monkeypatch.setattr(table_module, "_shared_reaches", expire_during_reachability)
    timed_out = table.get_route("A", "C")
    assert timed_out.status == "timeout"
    assert timed_out.polyline is None
    assert cache.stats().path_count == 1
    clock["now"] = 0.0
    monkeypatch.setattr(table_module, "_shared_reaches", original)
    assert DistanceTable(plan, stops, cfg, cache, deadline=100.0).get_route("A", "C").status == "success"


def test_timeout_during_leg_recovery_never_caches_disconnection(monkeypatch):
    from othello.routing import table as table_module

    inner = Polygon([(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)])
    plan = make_plan(inner=inner)
    stops = {"A": (1.123, 1.321), "B": (1.523, 1.321), "C": (5.321, 1.321)}
    cfg = make_config(grid_step_m=1.0, weights=SimpleNamespace(length=1.0, bend=0.5))
    clock = {"now": 0.0}
    monkeypatch.setattr(table_module.time, "monotonic", lambda: clock["now"])
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache, deadline=10.0)
    assert table.get_route("A", "B").status == "success"
    original = table_module.build_routing_graph

    def expire_in_builder(*args, **kwargs):
        clock["now"] = 10.0
        return original(*args, **kwargs)

    monkeypatch.setattr(table_module, "build_routing_graph", expire_in_builder)
    result = table.get_route("A", "C")
    assert result.status == "timeout" and result.polyline is None
    assert cache.stats().path_count == 1
    clock["now"] = 0.0
    monkeypatch.setattr(table_module, "build_routing_graph", original)
    recovered = DistanceTable(plan, stops, cfg, cache, deadline=100.0).get_route("A", "C")
    assert recovered.status == "success"
    assert recovered == DistanceTable(plan, stops, cfg, RoutingCache(), deadline=100.0).get_route("A", "C")


def test_shared_graph_changes_with_geometry_config_and_terminals():
    from conftest import make_opening

    plan = make_plan(inner=box(0, 0, 10, 10))
    stops = {"A": (1, 2), "B": (2, 2), "C": (9, 2)}
    cfg = make_config(grid_step_m=1.0, weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)
    assert table.get_route("A", "B").status == "success"
    assert table.get_route("A", "C").status == "success"
    initial_graphs = cache.stats().graph_count

    plan.walls_m = box(4, 0, 6, 10)
    plan.free_space_m = plan.inner_m.difference(plan.walls_m)
    blocked = table.get_route("A", "C")
    assert blocked.status == "unreachable_on_grid"
    assert blocked == DistanceTable(plan, stops, cfg, RoutingCache()).get_route("A", "C")
    assert cache.stats().graph_count == initial_graphs

    plan.walls_m = make_plan().walls_m
    plan.free_space_m = plan.inner_m
    plan.openings["bad"] = make_opening("bad", ["R1", "missing"], [(5, 4), (5, 6)])
    unsupported = table.get_route("A", "C")
    assert unsupported.status == "unsupported_geometry"
    assert unsupported == DistanceTable(plan, stops, cfg, RoutingCache()).get_route("A", "C")
    plan.openings.clear()
    cfg.grid_step_m = 0.5
    table.stops["C"] = (8.5, 3.5)
    changed = table.get_route("A", "C")
    assert changed == DistanceTable(plan, table.stops, cfg, RoutingCache()).get_route("A", "C")
    assert changed.status == "success"
    assert cache.stats().graph_count > initial_graphs


def test_plan_wide_unsupported_geometry_is_reused_and_content_invalidated(monkeypatch):
    from othello.routing import table as table_module

    plan = make_plan(inner=box(0, 0, 10, 10), rooms=[
        make_room("R1", box(0, 0, 6, 10)), make_room("R2", box(5, 0, 10, 10)),
    ])
    stops = {"A": (1, 1), "B": (2, 2), "C": (3, 3)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache(max_graphs=2)
    original = table_module.build_routing_graph
    calls = []

    def counted(*args):
        calls.append((args[2], args[3]))
        return original(*args)

    monkeypatch.setattr(table_module, "build_routing_graph", counted)
    table = DistanceTable(plan, stops, cfg, cache)
    first = table.get_route("A", "B")
    assert first.status == "unsupported_geometry" and "Overlapping room interiors" in first.diagnostics[0]
    assert table.get_route("B", "C") == first
    assert len(calls) == 1
    stops["C"] = (3.5, 3.5)
    table.stops["C"] = stops["C"]
    assert table.get_route("B", "C").status == "unsupported_geometry"
    assert len(calls) == 2
    cfg.grid_step_m = 0.5
    assert table.get_route("B", "C").status == "unsupported_geometry"
    assert len(calls) == 3
    assert cache.stats().graph_count <= cache.max_graphs
    cache.invalidate(plan.plan_id)
    assert table.get_route("B", "C").status == "unsupported_geometry"
    assert len(calls) == 4
    plan.rooms["R1"].polygon_m = box(0, 0, 5, 10)
    recovered = table.get_route("A", "B")
    assert recovered.status == "success"
    assert recovered == DistanceTable(plan, table.stops, cfg, RoutingCache()).get_route("A", "B")
    assert len(calls) == 6  # recovered query plus fresh-cache oracle


def test_terminal_unsupported_geometry_and_timeout_are_not_shared_failures(monkeypatch):
    from othello.routing import table as table_module
    from othello.routing.types import GraphBuildResult

    plan = make_plan(inner=box(0, 0, 10, 10), rooms=[
        make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10)),
    ])
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    calls = []
    original = table_module.build_routing_graph

    def counted(*args):
        calls.append(1)
        return original(*args)

    monkeypatch.setattr(table_module, "build_routing_graph", counted)
    table = DistanceTable(plan, {"A": (1, 1), "B": (2, 2), "bad": (5, 5)}, cfg, RoutingCache())
    assert table.get_route("A", "B").status == "unsupported_geometry"
    assert table.get_route("A", "B").status == "unsupported_geometry"
    assert len(calls) == 1
    assert table.get_route("B", "A").status == "unsupported_geometry"
    assert len(calls) == 2

    def timed_out(*args):
        calls.append(1)
        return GraphBuildResult("timeout", diagnostics=("temporary deadline",))

    monkeypatch.setattr(table_module, "build_routing_graph", timed_out)
    table = DistanceTable(plan, {"A": (1, 1), "B": (2, 2)}, cfg, RoutingCache())
    assert table.get_route("A", "B").status == "timeout"
    assert table.get_route("A", "B").status == "timeout"
    assert len(calls) == 4


def test_cached_unsupported_opening_is_invalidated_by_opening_change():
    opening = make_opening("D", ["R1", "R1"], [(5, 4), (5, 6)])
    plan = make_plan(inner=box(0, 0, 10, 10), rooms=[
        make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10)),
    ], openings=[opening])
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    table = DistanceTable(plan, {"A": (1, 5), "B": (9, 5)}, cfg, RoutingCache())
    bad = table.get_route("A", "B")
    assert bad.status == "unsupported_geometry" and "two interior rooms" in bad.diagnostics[0]
    opening.rooms = ["R1", "R2"]
    recovered = table.get_route("A", "B")
    assert recovered.status == "success" and recovered.opening_ids == ("D",)


@pytest.mark.parametrize("case,expected_status", [
    ("outside_free_space", "unsupported_geometry"),
    ("inside_window", "unreachable_on_grid"),
    ("inside_wall", "unreachable_on_grid"),
    ("ambiguous_room_boundary", "unsupported_geometry"),
])
def test_invalid_coincident_self_query_never_succeeds(case, expected_status):
    plan = make_plan(inner=box(0, 0, 10, 10))
    point = (5.0, 5.0)
    if case == "outside_free_space":
        point = (-1.0, 5.0)
    elif case == "inside_window":
        plan = make_plan(inner=box(0, 0, 10, 10), windows=box(4, 4, 6, 6))
    elif case == "inside_wall":
        plan = make_plan(inner=box(0, 0, 10, 10), walls=box(4, 4, 6, 6))
    else:
        plan = make_plan(inner=box(0, 0, 10, 10), rooms=[
            make_room("R1", box(0, 0, 5, 10)),
            make_room("R2", box(5, 0, 10, 10)),
        ])

    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    table = DistanceTable(plan, {"X": point}, cfg, RoutingCache())
    result = table.get_route("X", "X")
    assert result.status == expected_status
    assert result.polyline is None
    assert result.length_m is None
    assert result.diagnostics


def test_standardized_timeout_and_status_fidelity(monkeypatch):
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()

    # Initialize table when deadline has not expired
    monkeypatch.setattr("time.monotonic", lambda: 0.0)
    table = DistanceTable(plan, stops, cfg, cache, deadline=10.0)

    # Force subsequent query execution to experience timeout
    monkeypatch.setattr("time.monotonic", lambda: 20.0)

    # get_route returns TableRouteResult with status="timeout"
    route = table.get_route("H1", "N1")
    assert route.status == "timeout"
    assert route.diagnostics

    # get_distance raises RoutingFailureError on timeout
    with pytest.raises(RoutingFailureError) as exc_info:
        table.get_distance("H1", "N1")
    assert exc_info.value.status == "timeout"

    # get_distance with explicit default returns fallback
    assert table.get_distance("H1", "N1", default=float("inf")) == float("inf")

    # get_cost raises RoutingFailureError on timeout
    with pytest.raises(RoutingFailureError) as exc_cost:
        table.get_cost("H1", "N1")
    assert exc_cost.value.status == "timeout"
    assert table.get_cost("H1", "N1", default=-1.0) == -1.0


def test_unreachable_on_grid_status_propagation():
    # Disconnected rooms without openings
    plan = make_plan(
        inner=box(0, 0, 10, 10),
        walls=box(4, 0, 6, 10),  # Complete dividing wall, no openings
        rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
    )
    stops = {"H1": (1, 2), "N1": (9, 2)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)

    route = table.get_route("H1", "N1")
    assert route.status == "unreachable_on_grid"

    with pytest.raises(RoutingFailureError) as exc:
        table.get_distance("H1", "N1")
    assert exc.value.status == "unreachable_on_grid"

    # Default fallback returns value without raising
    assert table.get_distance("H1", "N1", default=float("inf")) == float("inf")


def test_distance_matrix_and_summary_hit_rate():
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2), "N2": (1, 8)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)

    matrix = table.distance_matrix()
    assert len(matrix) == 9  # 3 x 3
    for s in stops:
        assert matrix[(s, s)] == 0.0

    # Off diagonals are positive
    assert matrix[("H1", "N1")] > 0.0
    assert matrix[("H1", "N2")] > 0.0

    # Query matrix again: must be 100% cache hits
    matrix2 = table.distance_matrix()
    assert matrix2 == matrix

    stats = cache.stats()
    assert stats.path_hits > 0
    assert "path_hit_rate=" in stats.summary()


def test_local_memo_invalidates_when_plan_geometry_mutates():
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, cfg, cache)

    r1 = table.get_route("H1", "N1")
    assert r1.status == "success"

    # Completely divide rooms with full-height wall and no openings
    plan.walls_m = box(4, 0, 6, 10)
    plan.free_space_m = plan.inner_m.difference(plan.walls_m)
    plan.openings.clear()
    # Query again -> detects fingerprint mismatch in _make_path_key, produces new key
    r2 = table.get_route("H1", "N1")
    assert r2.status == "unreachable_on_grid"


def test_config_changes_isolate_cache():
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2)}
    cache = RoutingCache()

    # Route with config 1 (bend weight = 0.1)
    cfg1 = make_config(weights=SimpleNamespace(length=1.0, bend=0.1))
    table1 = DistanceTable(plan, stops, cfg1, cache)
    r1 = table1.get_route("H1", "N1")
    assert r1.status == "success"

    # Route with config 2 (bend weight = 2.0) -> different PathKey
    cfg2 = make_config(weights=SimpleNamespace(length=1.0, bend=2.0))
    table2 = DistanceTable(plan, stops, cfg2, cache)
    r2 = table2.get_route("H1", "N1")
    assert r2.status == "success"

    # Should have 2 path misses and 0 hits across different configs
    stats = cache.stats()
    assert stats.path_misses == 2
    assert stats.path_hits == 0
    assert stats.graph_hits >= 1  # Weights change A*, not the selected Hanan graph.


def test_terminal_set_changes_isolate_graph_cache():
    plan = sample_plan()
    cache = RoutingCache()
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))

    # Table A with 2 stops
    stops_a = {"H1": (1, 2), "N1": (9, 2)}
    table_a = DistanceTable(plan, stops_a, cfg, cache)
    r_a = table_a.get_route("H1", "N1")
    assert r_a.status == "success"
    assert cache.stats().graph_misses == 2

    # Table B with 3 stops -> different terminal coordinates signature in GraphKey
    stops_b = {"H1": (1, 2), "N1": (9, 2), "N2": (3, 3)}
    table_b = DistanceTable(plan, stops_b, cfg, cache)
    r_b = table_b.get_route("H1", "N1")
    assert r_b.status == "success"
    # Produces a new GraphKey because terminals set changed coordinate grid lines
    assert cache.stats().graph_misses == 4


def test_cached_vs_uncached_strict_equivalence():
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))

    # Uncached run
    cache_empty = RoutingCache()
    table_uncached = DistanceTable(plan, stops, cfg, cache_empty)
    res_uncached = table_uncached.get_route("H1", "N1")
    assert res_uncached.status == "success"

    # Cached run
    cache_populated = RoutingCache()
    table_cached = DistanceTable(plan, stops, cfg, cache_populated)
    _ = table_cached.get_route("H1", "N1")  # prime cache
    res_cached = table_cached.get_route("H1", "N1")  # hit

    # Check complete equivalence across all fields
    assert res_cached.status == res_uncached.status
    assert res_cached.polyline == res_uncached.polyline
    assert res_cached.length_m == res_uncached.length_m
    assert res_cached.cost == res_uncached.cost
    assert res_cached.internal_bends == res_uncached.internal_bends
    assert res_cached.initial_bend == res_uncached.initial_bend
    assert res_cached.opening_ids == res_uncached.opening_ids
    assert res_cached.first_direction == res_uncached.first_direction
    assert res_cached.last_direction == res_uncached.last_direction
    assert res_cached.diagnostics == res_uncached.diagnostics


def test_timeout_never_cached_allows_subsequent_success(monkeypatch):
    plan = sample_plan()
    stops = {"H1": (1, 2), "N1": (9, 2)}
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()

    # Query 1 times out
    monkeypatch.setattr("time.monotonic", lambda: 0.0)
    table1 = DistanceTable(plan, stops, cfg, cache, deadline=5.0)
    monkeypatch.setattr("time.monotonic", lambda: 10.0)
    r1 = table1.get_route("H1", "N1")
    assert r1.status == "timeout"
    assert cache.stats().path_count == 0  # Timeout must NEVER be cached

    # Query 2 with generous deadline succeeds
    monkeypatch.setattr("time.monotonic", lambda: 0.0)
    table2 = DistanceTable(plan, stops, cfg, cache, deadline=100.0)
    r2 = table2.get_route("H1", "N1")
    assert r2.status == "success"
    assert cache.stats().path_count == 1



