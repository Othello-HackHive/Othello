import networkx as nx
import pytest
from shapely.geometry import box

from othello.routing import build_routing_graph
from othello.routing import grid
from conftest import make_config, make_opening, make_plan, make_room


def transit_plan(detour=False):
    rooms = [make_room("R1", box(0, 0, 3, 6)), make_room("X", box(3, 0, 7, 6), excluded=True),
             make_room("R2", box(7, 0, 10, 6))]
    openings = [make_opening("D1", ["R1", "X"], [(3, 1), (3, 3)]),
                make_opening("D2", ["X", "R2"], [(7, 1), (7, 3)])]
    if detour:
        rooms.append(make_room("R3", box(0, 6, 10, 10)))
        openings.extend([make_opening("P1", ["R1", "R3"], [(1, 6), (2, 6)], "open_passage"),
                         make_opening("P2", ["R2", "R3"], [(8, 6), (9, 6)], "open_passage")])
    return make_plan(inner=box(0, 0, 10, 10 if detour else 6), rooms=rooms,
                     openings=openings, excluded=["X"])


@pytest.mark.parametrize("policy,status,mode,attempt_count", [
    ("forbid", "unreachable_on_grid", "permitted_only", 2),
    ("allow", "ready", "allow", 1),
    ("avoid_if_possible", "ready", "allow", 3),
])
def test_transit_policies(policy, status, mode, attempt_count, assert_edges_safe):
    plan, config = transit_plan(), make_config(cable_transit_policy=policy)
    result = build_routing_graph(plan, {"H1": (1, 2), "N1": (9, 2)}, "H1", "N1", config)
    assert result.status == status, result.diagnostics
    assert result.transit_mode == mode
    assert len(result.attempts) == attempt_count
    if status == "ready":
        assert_edges_safe(result, plan, config)
    if policy == "avoid_if_possible":
        assert [(a.transit_mode, a.fallback_used, a.connected) for a in result.attempts[:2]] == [
            ("permitted_only", False, False), ("permitted_only", True, False)]
        assert "grid-level evidence" in result.diagnostics[0]


def test_longer_permitted_route_wins_over_excluded_shortcut(assert_edges_safe):
    plan = transit_plan(detour=True)
    terminals = {"H1": (1, 2), "N1": (9, 2), "top_line": (1, 8)}
    config = make_config()
    restricted = build_routing_graph(plan, terminals, "H1", "N1", config)
    assert restricted.transit_mode == "permitted_only", restricted.diagnostics
    assert_edges_safe(restricted, plan, config)
    relaxed = build_routing_graph(plan, terminals, "H1", "N1", make_config(cable_transit_policy="allow"))
    assert relaxed.status == "ready", relaxed.diagnostics
    # Dijkstra here is only a test oracle to establish the fixture's tradeoff.
    restricted_length = nx.shortest_path_length(restricted.graph, terminals["H1"], terminals["N1"], weight="length_m")
    relaxed_length = nx.shortest_path_length(relaxed.graph, terminals["H1"], terminals["N1"], weight="length_m")
    assert restricted_length > relaxed_length


def test_restricted_fallback_prevents_relaxation(assert_edges_safe):
    plan = transit_plan(detour=True)
    config = make_config()
    result = build_routing_graph(plan, {"H1": (1, 2), "N1": (9, 2)}, "H1", "N1", config)
    assert result.status == "ready", result.diagnostics
    assert result.transit_mode == "permitted_only"
    assert result.fallback_used
    assert [a.connected for a in result.attempts] == [False, True]
    assert_edges_safe(result, plan, config)


def test_unrelated_disconnected_terminal_does_not_trigger_relaxation():
    plan = transit_plan()
    terminals = {"H1": (1, 1), "N1": (2, 2), "unrelated": (9, 2)}
    result = build_routing_graph(plan, terminals, "H1", "N1", make_config())
    assert result.status == "ready"
    assert result.transit_mode == "permitted_only"
    assert len(result.attempts) == 1
    assert not nx.has_path(result.graph, terminals["H1"], terminals["unrelated"])


@pytest.mark.parametrize("phase", [
    "terminal validation", "geometry validation", "coordinate generation", "graph construction",
    "candidate enumeration", "edge validation", "connectivity traversal", "connectivity completion",
    "fallback coordinate generation",
])
def test_timeout_in_each_phase_never_relaxes(monkeypatch, phase):
    clock = {"now": 0.0}
    monkeypatch.setattr(grid.time, "monotonic", lambda: clock["now"])
    original_check = grid._Budget.check
    def check(self, current_phase):
        if current_phase == phase:
            clock["now"] = 10.0
        original_check(self, current_phase)
    monkeypatch.setattr(grid._Budget, "check", check)
    modes = []
    original_build = grid._build_graph
    def track(*args):
        modes.append(args[-2])
        return original_build(*args)
    monkeypatch.setattr(grid, "_build_graph", track)
    result = build_routing_graph(transit_plan(), {"H1": (1, 2), "N1": (9, 2)},
                                 "H1", "N1", make_config(), deadline=10.0)
    assert result.status == "timeout"
    assert result.graph is None
    assert phase in result.diagnostics[0]
    assert "allow" not in modes
    assert all(attempt.transit_mode == "permitted_only" for attempt in result.attempts)


def test_unsupported_geometry_never_relaxes(monkeypatch):
    plan = transit_plan()
    plan.rooms["overlap"] = make_room("overlap", box(0, 0, 2, 2))
    def unexpected(*args):
        pytest.fail("Unsupported geometry must not reach graph construction")
    monkeypatch.setattr(grid, "_build_graph", unexpected)
    result = build_routing_graph(plan, {"H1": (1, 2), "N1": (9, 2)}, "H1", "N1", make_config())
    assert result.status == "unsupported_geometry"
    assert not result.attempts


def test_timeout_after_restricted_exhaustion_does_not_start_relaxed_graph(monkeypatch):
    clock = {"now": 0.0, "builds": 0}
    monkeypatch.setattr(grid.time, "monotonic", lambda: clock["now"])
    original = grid._Budget.check
    def check(self, phase):
        if phase == "graph construction":
            clock["builds"] += 1
            if clock["builds"] == 3:
                clock["now"] = 10.0
        original(self, phase)
    monkeypatch.setattr(grid._Budget, "check", check)
    original_build = grid._build_graph
    def restricted_only(*args):
        assert args[-2] == "permitted_only"
        return original_build(*args)
    monkeypatch.setattr(grid, "_build_graph", restricted_only)
    result = build_routing_graph(transit_plan(), {"H1": (1, 2), "N1": (9, 2)},
                                 "H1", "N1", make_config(), deadline=10)
    assert result.status == "timeout"
    assert result.graph is None
    assert len(result.attempts) == 2
