"""Tests for Task 3.3 RoutingCache, geometry content hashing, and immutability."""

from copy import deepcopy
import sys
from types import SimpleNamespace
from types import MappingProxyType

import pytest
from shapely.geometry import LineString, Point, box

from conftest import make_config, make_opening, make_plan, make_room
from othello.routing import (
    CacheStats,
    GraphKey,
    PathKey,
    RouteSearchResult,
    RoutingCache,
    ValidatedGraph,
    astar_route,
    build_routing_graph,
    plan_fingerprint,
    validate_graph,
)
from othello.routing.astar import _TimedOut


def sample_plan():
    return make_plan(
        inner=box(0, 0, 10, 10),
        walls=box(4, 0, 6, 8),
        rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
        openings=[make_opening("D1", ["R1", "R2"], [(5, 8.5), (5, 9.5)])],
    )


def test_fingerprint_detects_every_geometry_field_change():
    base = sample_plan()
    fp_base = plan_fingerprint(base)
    assert fp_base.startswith("mock:")

    # 1. Mutate wall
    mod_wall = sample_plan()
    mod_wall.walls_m = box(4, 0, 6, 8.2)
    assert plan_fingerprint(mod_wall) != fp_base

    # 2. Mutate window
    mod_window = sample_plan()
    mod_window.windows_m = box(1, 0, 2, 0.2)
    assert plan_fingerprint(mod_window) != fp_base

    # 3. Mutate opening gap
    mod_opening = sample_plan()
    mod_opening.openings["D1"] = make_opening("D1", ["R1", "R2"], [(5, 8.6), (5, 9.6)])
    assert plan_fingerprint(mod_opening) != fp_base

    # 4. Mutate opening room pair
    mod_opening_rooms = sample_plan()
    mod_opening_rooms.openings["D1"] = make_opening("D1", ["R1"], [(5, 8.5), (5, 9.5)])
    assert plan_fingerprint(mod_opening_rooms) != fp_base

    # 5. Mutate room boundary
    mod_room = sample_plan()
    mod_room.rooms["R1"] = make_room("R1", box(0, 0, 4.9, 10))
    assert plan_fingerprint(mod_room) != fp_base

    # 6. Mutate excluded room list
    mod_excluded = sample_plan()
    mod_excluded.excluded_interior_rooms = ["R2"]
    assert plan_fingerprint(mod_excluded) != fp_base

    # 7. Mutate fixed node
    mod_node = sample_plan()
    mod_node.nodes = {"N1": SimpleNamespace(point_m=(1.5, 2.5))}
    assert plan_fingerprint(mod_node) != fp_base

    # 8. Version salt
    mod_version = sample_plan()
    mod_version.geometry_version = 2
    assert plan_fingerprint(mod_version) != fp_base


def test_fingerprint_enforces_deadlines(monkeypatch):
    plan = sample_plan()
    # Before check
    with pytest.raises(_TimedOut, match="fingerprint generation"):
        plan_fingerprint(plan, deadline=-1.0)


def test_validated_graph_deep_immutability():
    plan = sample_plan()
    terminals = {"H1": (1, 2), "N1": (9, 2)}
    cfg = make_config()
    built = build_routing_graph(plan, terminals, "H1", "N1", cfg)
    assert built.status == "ready"

    val = validate_graph(built.graph)
    assert isinstance(val, ValidatedGraph)
    assert isinstance(val.vertices, frozenset)

    # Adjacency MappingProxyType rejects mutation
    sample_coord = next(iter(val.adjacency))
    with pytest.raises(TypeError):
        val.adjacency[sample_coord] = ()

    # Neighbor sequences are immutable tuples
    neighbors = val.adjacency[sample_coord]
    assert isinstance(neighbors, tuple)

    # Calling validate_graph on ValidatedGraph is idempotent
    assert validate_graph(val) is val


def test_deterministic_cache_hits_and_spy_verification(monkeypatch):
    cache = RoutingCache(max_paths=10, max_graphs=4)
    plan = sample_plan()
    cfg = make_config(weights=SimpleNamespace(length=1.0, bend=0.5))
    terminals = {"H1": (1, 2), "N1": (9, 2)}
    built = build_routing_graph(plan, terminals, "H1", "N1", cfg)
    val_graph = validate_graph(built.graph)

    graph_key = GraphKey(
        plan_sig=plan_fingerprint(plan),
        policy=cfg.cable_transit_policy,
        cable_wall_margin_m=cfg.cable_wall_margin_m,
        grid_step_m=cfg.grid_step_m,
        tolerance_m=cfg.tolerance_m,
        terminals=tuple(sorted(terminals.items())),
        leg=("H1", "N1"),
    )
    path_key = PathKey(
        graph_key=graph_key,
        start=terminals["H1"],
        goal=terminals["N1"],
        initial_direction=None,
        length_weight=cfg.weights.length,
        bend_weight=cfg.weights.bend,
    )

    # Miss
    assert cache.get_graph(graph_key) is None
    assert cache.get_route(path_key) is None
    stats = cache.stats()
    assert stats.graph_misses == 1 and stats.path_misses == 1
    assert stats.graph_hits == 0 and stats.path_hits == 0

    # Store
    cache.put_graph(graph_key, val_graph)
    res = astar_route(val_graph, terminals["H1"], terminals["N1"], cfg)
    assert res.status == "success"
    cache.put_route(path_key, res)

    # Hit
    cached_graph = cache.get_graph(graph_key)
    assert cached_graph is val_graph
    cached_route = cache.get_route(path_key)
    assert cached_route is not None
    assert cached_route.status == "success"
    assert cached_route.polyline == res.polyline
    assert cached_route.cost == res.cost

    stats2 = cache.stats()
    assert stats2.graph_hits == 1 and stats2.path_hits == 1
    assert stats2.path_hit_rate == 0.5
    assert "path_hit_rate=50.0%" in stats2.summary()


def test_mutation_isolation_on_cached_route():
    cache = RoutingCache()
    graph_key = GraphKey("plan:1", "forbid", 0.02, 1.0, 0.001, (("A", (0, 0)), ("B", (2, 0))), None)
    path_key = PathKey(graph_key, (0, 0), (2, 0), None, 1.0, 0.5)

    res = RouteSearchResult("success", [(0, 0), (2, 0)], 2.0, 2.0, 0, 0, (), "E", "E")
    cache.put_route(path_key, res)

    # Mutate retrieved polyline
    retrieved1 = cache.get_route(path_key)
    retrieved1.polyline.append((999.0, 999.0))
    retrieved1.polyline[0] = (-888.0, -888.0)

    # Second retrieval must be pristine
    retrieved2 = cache.get_route(path_key)
    assert retrieved2.polyline == [(0, 0), (2, 0)]


def test_lru_eviction_for_paths_and_graphs():
    cache = RoutingCache(max_paths=2, max_graphs=2)
    gk1 = GraphKey("sig1", "forbid", 0.02, 1.0, 0.001, (), None)
    gk2 = GraphKey("sig2", "forbid", 0.02, 1.0, 0.001, (), None)
    gk3 = GraphKey("sig3", "forbid", 0.02, 1.0, 0.001, (), None)

    vg = ValidatedGraph({}, frozenset(), 0, 0)
    cache.put_graph(gk1, vg)
    cache.put_graph(gk2, vg)
    assert cache.stats().graph_count == 2
    assert cache.stats().evictions == 0

    # 3rd graph evicts gk1
    cache.put_graph(gk3, vg)
    assert cache.stats().graph_count == 2
    assert cache.stats().evictions == 1
    assert cache.get_graph(gk1) is None
    assert cache.get_graph(gk2) is vg
    assert cache.get_graph(gk3) is vg


def test_only_allowed_statuses_are_cached():
    cache = RoutingCache()
    gk = GraphKey("sig", "forbid", 0.02, 1.0, 0.001, (), None)
    pk = PathKey(gk, (0, 0), (1, 0), None, 1.0, 0.5)

    # Timeouts and unsupported geometry must not be cached
    cache.put_route(pk, RouteSearchResult("timeout", diagnostics=("Timeout",)))
    assert cache.get_route(pk) is None

    cache.put_route(pk, RouteSearchResult("unsupported_geometry", diagnostics=("Ambiguous",)))
    assert cache.get_route(pk) is None

    # Allowed: success, unreachable, unreachable_on_grid
    cache.put_route(pk, RouteSearchResult("unreachable_on_grid", diagnostics=("Disconnected",)))
    res = cache.get_route(pk)
    assert res is not None and res.status == "unreachable_on_grid"

    cache.put_route(pk, RouteSearchResult("unreachable", diagnostics=("Exhausted",)))
    res = cache.get_route(pk)
    assert res is not None and res.status == "unreachable"


def test_deadline_enforced_on_cached_route_hit():
    cache = RoutingCache()
    gk = GraphKey("sig", "forbid", 0.02, 1.0, 0.001, (), None)
    pk = PathKey(gk, (0, 0), (1, 0), None, 1.0, 0.5)
    cache.put_route(pk, RouteSearchResult("success", [(0, 0), (1, 0)], 1.0, 1.0, 0, 0, (), "E", "E"))

    # When deadline is in the past, hit must fail with _TimedOut
    with pytest.raises(_TimedOut, match="cache lookup"):
        cache.get_route(pk, deadline=-1.0)


def test_avoid_if_possible_strict_leg_isolation():
    from test_transit_policy import transit_plan
    plan = transit_plan(detour=True)
    cfg = make_config(grid_step_m=1.0, cable_transit_policy="avoid_if_possible")

    # Leg 1: (1, 2) to (9, 2) has a wall blocking direct travel; detour connects permitted_only
    # Leg 2: blocked completely without excluded room
    terminals = {"H1": (1, 2), "N1": (9, 2), "N2": (5, 5)}
    gk_leg1 = GraphKey("plan:sig", "avoid_if_possible", 0.02, 1.0, 0.001, tuple(sorted(terminals.items())), ("H1", "N1"))
    gk_leg2 = GraphKey("plan:sig", "avoid_if_possible", 0.02, 1.0, 0.001, tuple(sorted(terminals.items())), ("H1", "N2"))

    assert gk_leg1 != gk_leg2
    assert gk_leg1.leg == ("H1", "N1")
    assert gk_leg2.leg == ("H1", "N2")


def test_invalidate_plan_id_selective_clear():
    cache = RoutingCache()
    gk_a = GraphKey("planA:123", "forbid", 0.02, 1.0, 0.001, (), None)
    gk_b = GraphKey("planB:456", "forbid", 0.02, 1.0, 0.001, (), None)
    pk_a = PathKey(gk_a, (0, 0), (1, 0), None, 1.0, 0.5)
    pk_b = PathKey(gk_b, (0, 0), (1, 0), None, 1.0, 0.5)

    vg = ValidatedGraph({}, frozenset(), 0, 0)
    res = RouteSearchResult("success", [(0, 0), (1, 0)], 1.0, 1.0, 0, 0, (), "E", "E")

    cache.put_graph(gk_a, vg)
    cache.put_graph(gk_b, vg)
    cache.put_route(pk_a, res)
    cache.put_route(pk_b, res)

    # Invalidate planA only
    cache.invalidate("planA")
    assert cache.get_graph(gk_a) is None
    assert cache.get_route(pk_a) is None
    assert cache.get_graph(gk_b) is vg
    assert cache.get_route(pk_b) is not None

    # Full invalidate
    cache.invalidate()
    assert cache.get_graph(gk_b) is None
    assert cache.get_route(pk_b) is None


def test_memory_usage_reporting():
    cache = RoutingCache()
    gk = GraphKey("sig", "forbid", 0.02, 1.0, 0.001, (), None)
    pk = PathKey(gk, (0, 0), (1, 0), None, 1.0, 0.5)
    cache.put_graph(gk, ValidatedGraph({}, frozenset([(0, 0), (1, 0)]), 2, 1))
    cache.put_route(pk, RouteSearchResult("success", [(0, 0), (1, 0)], 1.0, 1.0, 0, 0, (), "E", "E"))

    mem = cache.memory_usage_bytes()
    assert mem["paths_bytes"] > 0
    assert mem["graphs_bytes"] > 0
    assert mem["total_bytes"] == mem["paths_bytes"] + mem["graphs_bytes"]


def test_memory_usage_counts_proxy_backing_and_shared_graph_once():
    cache = RoutingCache()
    empty = cache.memory_usage_bytes()["graphs_bytes"]
    adjacency = {(float(index), 0.0): () for index in range(300)}
    graph = ValidatedGraph(MappingProxyType(adjacency), frozenset(adjacency), 300, 0)
    first_key = GraphKey("sig:one", "forbid", 0.02, 1.0, 0.001, (), ("A", "B"))
    second_key = GraphKey("sig:two", "forbid", 0.02, 1.0, 0.001, (), ("A", "C"))

    cache.put_graph(first_key, graph)
    one = cache.memory_usage_bytes()["graphs_bytes"]
    # The retained proxy owns a dictionary in addition to its small wrapper.
    assert one - empty > sys.getsizeof(dict(graph.adjacency))

    cache.put_graph(second_key, graph)
    two = cache.memory_usage_bytes()["graphs_bytes"]
    assert two > one
    assert two - one < (one - empty) / 10
