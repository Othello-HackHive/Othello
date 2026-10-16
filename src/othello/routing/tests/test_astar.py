"""A* tests with an independently constructed direction-expanded oracle."""

from copy import deepcopy
from math import fsum, nextafter
import random
from types import SimpleNamespace

import networkx as nx
import pytest
from shapely.geometry import LineString, box

from othello.routing import RouteSearchResult, astar_route, build_routing_graph
from othello.routing import astar


def test_fewest_bends_breaks_ties_by_shorter_length():
    graph = nx.DiGraph()
    for y in (-3, -1):
        points = [(0, 0), (0, y), (4, y), (4, 0)]
        for start, end in zip(points, points[1:]):
            add_arc(graph, start, end)
    result = astar.fewest_bends_then_length(graph, (0, 0), (4, 0))
    assert result.status == "success"
    assert result.internal_bends == 2
    assert result.length_m == 6
    assert result.polyline == [(0, 0), (0, -1), (4, -1), (4, 0)]
from conftest import make_config, make_opening, make_plan, make_room

VECTORS = {"E": (1, 0), "W": (-1, 0), "N": (0, 1), "S": (0, -1)}


def config(length=1.0, bend=0.5):
    return SimpleNamespace(weights=SimpleNamespace(length=length, bend=bend))


def direction(start, end):
    dx, dy = end[0] - start[0], end[1] - start[1]
    assert (dx == 0) != (dy == 0)
    if dx:
        return "E" if dx > 0 else "W"
    return "N" if dy > 0 else "S"


def add_arc(graph, start, end, ids=()):
    graph.add_edge(start, end, direction=direction(start, end),
                   length_m=abs(end[0] - start[0]) + abs(end[1] - start[1]), opening_ids=tuple(ids))


def path_graph(points, ids=None):
    graph = nx.DiGraph()
    graph.add_nodes_from(points)
    for index, (start, end) in enumerate(zip(points, points[1:])):
        add_arc(graph, start, end, ids[index] if ids else ())
    return graph


def dijkstra_cost(graph, start, goal, cfg, initial=None):
    """Explicit expansion, independent of A* state/heuristic/cost helpers."""
    expanded = nx.DiGraph()
    source, sink = "source", "sink"
    expanded.add_nodes_from([source, sink])
    expanded.add_nodes_from((vertex, heading) for vertex in graph for heading in VECTORS)
    for u, v, data in graph.edges(data=True):
        travel = direction(u, v)
        distance = abs(v[0] - u[0]) + abs(v[1] - u[1])
        for heading in VECTORS:
            expanded.add_edge((u, heading), (v, travel),
                              weight=cfg.weights.length * distance + cfg.weights.bend * (heading != travel))
    for v in graph.successors(start):
        travel = direction(start, v)
        distance = abs(v[0] - start[0]) + abs(v[1] - start[1])
        expanded.add_edge(source, (v, travel), weight=cfg.weights.length * distance
                          + cfg.weights.bend * (initial is not None and initial != travel))
    for heading in VECTORS:
        expanded.add_edge((goal, heading), sink, weight=0)
    if start == goal:
        expanded.add_edge(source, sink, weight=0)
    try:
        return nx.dijkstra_path_length(expanded, source, sink, weight="weight")
    except nx.NetworkXNoPath:
        return None


def assert_accounting(result, start, goal, cfg, initial=None):
    assert result.status == "success"
    assert result.polyline[0] == start
    assert result.polyline[-1] == goal
    directions = [direction(a, b) for a, b in zip(result.polyline, result.polyline[1:])]
    length = fsum(abs(b[0] - a[0]) + abs(b[1] - a[1])
                  for a, b in zip(result.polyline, result.polyline[1:]))
    bends = sum(a != b for a, b in zip(directions, directions[1:]))
    initial_bend = int(bool(directions) and initial is not None and initial != directions[0])
    assert result.length_m == pytest.approx(length)
    assert result.internal_bends == bends
    assert result.initial_bend == initial_bend
    assert result.first_direction == (directions[0] if directions else None)
    assert result.last_direction == (directions[-1] if directions else None)
    assert result.cost == pytest.approx(cfg.weights.length * length + cfg.weights.bend * (bends + initial_bend))
    assert result.diagnostics == ()


def assert_matches_oracle(graph, start, goal, cfg, initial=None):
    expected = dijkstra_cost(graph, start, goal, cfg, initial)
    result = astar_route(graph, start, goal, cfg, initial_direction=initial)
    if expected is None:
        assert result.status == "unreachable"
        assert_failure_payload(result)
    else:
        assert_accounting(result, start, goal, cfg, initial)
        assert result.cost == pytest.approx(expected, rel=1e-12, abs=1e-12)
    return result


def assert_failure_payload(result):
    for field in ("polyline", "length_m", "cost", "internal_bends", "initial_bend", "opening_ids",
                  "first_direction", "last_direction"):
        assert getattr(result, field) is None
    assert result.diagnostics


@pytest.mark.parametrize("initial", [None, *VECTORS])
@pytest.mark.parametrize("heading", VECTORS)
def test_every_initial_heading_and_directionless_start(initial, heading):
    start, goal = (0, 0), VECTORS[heading]
    result = assert_matches_oracle(path_graph([start, goal]), start, goal, config(bend=2), initial)
    turn = int(initial is not None and initial != heading)
    assert result.cost == 1 + 2 * turn
    assert result.initial_bend == turn
    assert result.internal_bends == 0


@pytest.mark.parametrize("first", VECTORS)
@pytest.mark.parametrize("second", VECTORS)
def test_corners_same_direction_and_reversals(first, second):
    start = (0, 0)
    mid = tuple(2 * value for value in VECTORS[first])
    goal = tuple(a + b for a, b in zip(mid, VECTORS[second]))
    result = assert_matches_oracle(path_graph([start, mid, goal]), start, goal, config())
    assert result.internal_bends == int(first != second)
    assert result.polyline == ([start, goal] if first == second else [start, mid, goal])


def tradeoff_graph():
    short = [(0, 0), (1, 0), (1, 1), (2, 1), (2, 2), (4, 2)]
    long = [(0, 0), (0, 3), (4, 3), (4, 2)]
    return nx.compose(path_graph(short), path_graph(long))


@pytest.mark.parametrize("length,bend,expected_length,expected_bends", [
    (1, 0, 6, 4), (1, 0.5, 6, 4), (1, 2, 8, 2), (0, 1, 8, 2),
])
def test_length_bend_tradeoffs(length, bend, expected_length, expected_bends):
    result = assert_matches_oracle(tradeoff_graph(), (0, 0), (4, 2), config(length, bend))
    assert result.length_m == expected_length
    assert result.internal_bends == expected_bends


def test_distinct_arrival_headings_at_one_vertex_must_survive():
    # Both prefixes cost 4.5. Keeping only the first (north-facing) arrival at
    # (2,2) loses the optimum because its final eastward edge adds another bend.
    graph = nx.compose(path_graph([(0, 0), (2, 0), (2, 2), (3, 2)]),
                       path_graph([(0, 0), (0, 2), (2, 2)]))
    result = assert_matches_oracle(graph, (0, 0), (3, 2), config())
    assert result.cost == 5.5
    assert result.first_direction == "N"
    assert result.internal_bends == 1


def test_exact_endpoints_simplification_and_repeated_opening_ids():
    points = [(0.123456789, 0.25), (1.25, 0.25), (2.75, 0.25), (2.75, 1.125), (1.25, 1.125)]
    graph = path_graph(points, [("D1",), ("P1",), ("D1",), ("P2", "D2")])
    result = assert_matches_oracle(graph, points[0], points[-1], config())
    assert result.polyline == [points[0], points[2], points[3], points[4]]
    assert result.opening_ids == ("D1", "P1", "D1", "P2", "D2")


def test_adjacent_float_endpoints_remain_distinct():
    start, goal = (1.0, 2.0), (nextafter(1.0, 2.0), 2.0)
    result = astar_route(path_graph([start, goal]), start, goal, config())
    assert result.polyline == [start, goal]
    assert result.length_m == goal[0] - start[0] > 0


@pytest.mark.parametrize("initial", [None, *VECTORS])
def test_coincident_endpoints_have_no_travel_or_bends(initial):
    point = (1.123456789, -2.345678901)
    result = assert_matches_oracle(path_graph([point]), point, point, config(), initial)
    assert result == RouteSearchResult("success", [point], 0.0, 0.0, 0, 0, (), None, None)


def test_unreachable_and_zero_cost_cycles_terminate():
    graph = path_graph([(0, 0), (1, 0), (0, 0)])
    graph.add_node((4, 4))
    result = assert_matches_oracle(graph, (0, 0), (4, 4), config(0, 0))
    assert "not proven geometric infeasibility" in result.diagnostics[0]
    add_arc(graph, (1, 0), (1, 4))
    add_arc(graph, (1, 4), (4, 4))
    assert assert_matches_oracle(graph, (0, 0), (4, 4), config(0, 0)).cost == 0


@pytest.mark.parametrize("weights", [(1, 0.5), (0, 0), (0, 2), (2, 0)])
def test_determinism_under_shuffled_graph_insertion(weights):
    graph = tradeoff_graph()
    original = deepcopy(graph)
    expected = astar_route(graph, (0, 0), (4, 2), config(*weights))
    for seed in range(8):
        rng = random.Random(seed)
        nodes, edges = list(graph), list(graph.edges(data=True))
        rng.shuffle(nodes)
        rng.shuffle(edges)
        shuffled = nx.DiGraph()
        shuffled.add_nodes_from(nodes)
        shuffled.add_edges_from(edges)
        assert astar_route(shuffled, (0, 0), (4, 2), config(*weights)) == expected
    assert nx.utils.graphs_equal(graph, original)


@pytest.mark.parametrize("seed", range(12))
def test_seeded_small_graphs_match_explicit_dijkstra(seed):
    rng = random.Random(seed)
    graph = nx.DiGraph()
    graph.add_nodes_from((x, y) for x in range(4) for y in range(4))
    for u in list(graph):
        for dx, dy in VECTORS.values():
            v = (u[0] + dx, u[1] + dy)
            if v in graph and rng.random() < 0.65:
                add_arc(graph, u, v)
    for length, bend in ((1, 0.5), (1, 3), (0, 1), (1, 0), (0, 0)):
        for initial in (None, *VECTORS):
            assert_matches_oracle(graph, (0, 0), (3, 3), config(length, bend), initial)


@pytest.mark.parametrize("field", ["length", "bend"])
@pytest.mark.parametrize("value", [-1, float("inf"), float("nan"), True, "1"])
def test_invalid_weights(field, value):
    cfg = config()
    setattr(cfg.weights, field, value)
    with pytest.raises(ValueError):
        astar_route(path_graph([(0, 0), (2, 0)]), (0, 0), (2, 0), cfg)


@pytest.mark.parametrize("attribute,value", [
    ("length_m", 0), ("length_m", -1), ("length_m", 1.5), ("length_m", float("inf")),
    ("length_m", float("nan")), ("length_m", True), ("direction", "N"), ("direction", None),
    ("opening_ids", "D1"), ("opening_ids", (123,)),
])
def test_invalid_edge_attributes(attribute, value):
    graph = path_graph([(0, 0), (2, 0)])
    graph[(0, 0)][(2, 0)][attribute] = value
    with pytest.raises(ValueError):
        astar_route(graph, (0, 0), (2, 0), config())


@pytest.mark.parametrize("attribute", ["length_m", "direction", "opening_ids"])
def test_missing_edge_attributes(attribute):
    graph = path_graph([(0, 0), (2, 0)])
    del graph[(0, 0)][(2, 0)][attribute]
    with pytest.raises(ValueError, match="Missing Task 3.1"):
        astar_route(graph, (0, 0), (2, 0), config())


def test_invalid_graph_shapes_and_call_contract():
    graph = path_graph([(0, 0), (2, 0)])
    for bad_graph in (nx.Graph(graph), nx.MultiDiGraph(graph), SimpleNamespace(status="unsupported_geometry")):
        with pytest.raises(ValueError, match="directed graph"):
            astar_route(bad_graph, (0, 0), (2, 0), config())
    for end in ((1, 1), (0, 0)):
        malformed = nx.DiGraph()
        malformed.add_edge((0, 0), end, length_m=2, direction="E", opening_ids=())
        with pytest.raises(ValueError, match="axis aligned"):
            astar_route(malformed, (0, 0), end, config())
    for bad_node in ("node", (float("nan"), 0), (0, 0, 0)):
        malformed = graph.copy()
        malformed.add_node(bad_node)
        with pytest.raises(ValueError):
            astar_route(malformed, (0, 0), (2, 0), config())
    with pytest.raises(ValueError, match="endpoints"):
        astar_route(graph, (0, 0), (3, 0), config())
    with pytest.raises(ValueError, match="initial_direction"):
        astar_route(graph, (0, 0), (2, 0), config(), initial_direction="NE")
    with pytest.raises(ValueError, match="weights"):
        astar_route(graph, (0, 0), (2, 0), SimpleNamespace())
    with pytest.raises(ValueError, match="deadline"):
        astar_route(graph, (0, 0), (2, 0), config(), deadline=float("nan"))
    with pytest.raises(ValueError, match="non-finite"):
        astar_route(graph, (0, 0), (2, 0), config(length=1.7e308))


@pytest.mark.parametrize("phase", ["validation", "expansion", "relaxation", "reconstruction", "completion"])
def test_deadline_during_each_phase_discards_partial_results(monkeypatch, phase):
    clock = {"now": 0.0, "visits": 0}
    monkeypatch.setattr(astar.time, "monotonic", lambda: clock["now"])
    original = astar._check_deadline
    def expire(deadline, current_phase):
        if current_phase == phase:
            clock["visits"] += 1
            if clock["visits"] >= (1 if phase == "completion" else 2):
                clock["now"] = 10.0
        original(deadline, current_phase)
    monkeypatch.setattr(astar, "_check_deadline", expire)
    graph = path_graph([(0, 0), (1, 0), (1, 1), (2, 1)])
    result = astar_route(graph, (0, 0), (2, 1), config(), deadline=10.0)
    assert result.status == "timeout"
    assert phase in result.diagnostics[0]
    assert_failure_payload(result)


def test_expired_deadline_precedes_coincident_endpoint_success(monkeypatch):
    monkeypatch.setattr(astar.time, "monotonic", lambda: 10.0)
    result = astar_route(path_graph([(0, 0)]), (0, 0), (0, 0), config(), deadline=10.0)
    assert result.status == "timeout"
    assert_failure_payload(result)


def test_offset_doorway_portal_graph_integration(assert_room_transitions_authorized):
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
                     openings=[make_opening("P1", ["R1", "R2"], [(5.0005, 0), (5.0005, 10)], "open_passage")])
    terminals = {"H1": (1, 5), "N1": (9, 5), "candidate": (5.00025, 8)}
    cfg = make_config(grid_step_m=0.10, weights=config().weights, cable_transit_policy="forbid")
    built = build_routing_graph(plan, terminals, "H1", "N1", cfg)
    assert built.status == "ready"
    assert_room_transitions_authorized(built.graph, plan, cfg)
    result = assert_matches_oracle(built.graph, terminals["H1"], terminals["N1"], cfg)
    assert result.polyline == [terminals["H1"], terminals["N1"]]
    assert result.opening_ids == ("P1",)
    assert plan.free_space_m.covers(LineString(result.polyline))
    assert result.first_direction == result.last_direction == "E"


def test_ring_junction_accounting_and_zero_length_heading_passthrough():
    square = [(0, 0), (2, 0), (2, 2), (0, 2), (0, 0)]
    graph = path_graph(square)
    cfg = config()
    legs, arriving = [], None
    for start, end in zip(square, square[1:]):
        result = astar_route(graph, start, end, cfg, initial_direction=arriving)
        assert result.status == "success"
        legs.append(result)
        arriving = result.last_direction
        zero = astar_route(graph, end, end, cfg, initial_direction=arriving)
        assert zero.first_direction is zero.last_direction is None
        assert zero.cost == zero.initial_bend == 0
        # No movement means retain the arriving heading for the next leg.
    assert sum(leg.internal_bends for leg in legs) == 0
    assert sum(leg.initial_bend for leg in legs) == 3
    closure = int(legs[-1].last_direction != legs[0].first_direction)
    assert closure == 1
    assert sum(leg.cost for leg in legs) + cfg.weights.bend * closure == 10.0
