from types import SimpleNamespace

import networkx as nx
import pytest
from shapely.affinity import translate
from shapely.geometry import LineString, MultiPolygon, Polygon, box

from othello.routing import build_routing_graph
from conftest import make_config, make_plan, make_room


def build(plan, config, terminals):
    return build_routing_graph(plan, terminals, "H1", "N1", config)


def test_exact_terminals_metadata_and_determinism(plan, config, terminals, assert_edges_safe):
    result = build(plan, config, terminals)
    assert result.terminal_vertices == terminals
    assert_edges_safe(result, plan, config)
    assert nx.has_path(result.graph, terminals["H1"], terminals["N1"])
    assert not result.fallback_used
    assert len(result.attempts) == 1
    again = build(plan, config, terminals)
    assert list(result.graph.nodes) == list(again.graph.nodes)
    assert list(result.graph.edges(data=True)) == list(again.graph.edges(data=True))


def test_close_and_coincident_terminals_are_not_snapped(plan, config):
    terminals = {"H1": (1.123456789, 2.0), "N1": (1.123456790, 2.0), "alias": (1.123456789, 2.0)}
    result = build(plan, config, terminals)
    assert result.status == "ready"
    assert result.terminal_vertices == terminals
    assert terminals["H1"] != terminals["N1"]
    assert result.graph.has_edge(terminals["H1"], terminals["N1"])
    assert not list(nx.selfloop_edges(result.graph))
    same = build_routing_graph(plan, terminals, "H1", "alias", config)
    assert same.status == "ready"


def test_node_lines_and_negative_coordinates(config, assert_edges_safe):
    plan = make_plan(inner=box(-10, -10, 0, 0), nodes={"fixed": SimpleNamespace(point_m=(-5.123, -6.987))})
    terminals = {"H1": (-9.1, -8.3), "N1": (-1.2, -2.4)}
    result = build(plan, config, terminals)
    assert (-5.123, -6.987) in result.graph
    assert_edges_safe(result, plan, config)


@pytest.mark.parametrize("kind", ["walls", "windows"])
def test_full_segments_cannot_cross_obstacles(kind, config, assert_edges_safe):
    plan = make_plan(**{kind: box(4, 3, 6, 7)})
    terminals = {"H1": (1, 5), "N1": (9, 5)}
    result = build(plan, config, terminals)
    assert_edges_safe(result, plan, config)
    assert not result.graph.has_edge(terminals["H1"], terminals["N1"])
    assert any(x < 4 for x, _ in result.graph)
    assert any(x > 6 for x, _ in result.graph)


@pytest.mark.parametrize("offset,can_cross", [(0.249, False), (0.25, True), (0.251, True)])
def test_search_clearance_threshold(offset, can_cross, assert_edges_safe):
    config = make_config(cable_wall_margin_m=0.25)
    plan = make_plan(walls=box(4, 0, 6, 3))
    y = 3 + offset
    result = build(plan, config, {"H1": (1, y), "N1": (9, y)})
    assert_edges_safe(result, plan, config)
    crossing = any(a[1] == b[1] == y and min(a[0], b[0]) <= 5 <= max(a[0], b[0])
                   for a, b in result.graph.edges)
    assert crossing == can_cross


@pytest.mark.parametrize("kind", ["walls", "windows"])
def test_default_clearance_is_translation_invariant(kind, assert_edges_safe):
    config = make_config(grid_step_m=0.10, cable_transit_policy="forbid")
    lengths = []
    for dx, dy in ((0, 0), (0, -4), (17.25, 31.5), (-31.25, -84.75)):
        inner = translate(box(0, 0, 10, 4.1), xoff=dx, yoff=dy)
        obstacle = translate(box(4, 0, 6, 4), xoff=dx, yoff=dy)
        plan = make_plan(inner=inner, **{kind: obstacle})
        terminals = {"H1": (1 + dx, 2 + dy), "N1": (9 + dx, 2 + dy)}
        witness = LineString([(1 + dx, 2 + dy), (1 + dx, 4.05 + dy),
                              (9 + dx, 4.05 + dy), (9 + dx, 2 + dy)])
        assert plan.free_space_m.covers(witness)
        assert witness.distance(obstacle) > config.cable_wall_margin_m
        result = build(plan, config, terminals)
        assert result.status == "ready", (dx, dy, result.diagnostics)
        assert not result.fallback_used
        assert result.terminal_vertices == terminals
        assert_edges_safe(result, plan, config)
        lengths.append(nx.shortest_path_length(result.graph, terminals["H1"], terminals["N1"],
                                              weight="length_m"))
    assert lengths == pytest.approx([lengths[0]] * len(lengths), rel=0, abs=1e-12)


@pytest.mark.parametrize("deficit", [1e-10, 1e-6, 0.0005])
@pytest.mark.parametrize("dy", [0, -4, 31.5])
def test_real_clearance_shortfalls_are_not_excused_by_geometric_tolerance(deficit, dy):
    config = make_config(cable_transit_policy="forbid", tolerance_m=0.001)
    plan = make_plan(inner=translate(box(3, 3, 7, 5), yoff=dy),
                     walls=translate(box(4, 3, 6, 4), yoff=dy))
    point = (5, 4 + dy + config.cable_wall_margin_m - deficit)
    result = build(plan, config, {"H1": point, "N1": point})
    assert result.status == "unreachable_on_grid"


@pytest.mark.parametrize("shape", [
    Polygon([(0, 0), (8, 0), (8, 8), (0, 8)], holes=[[(3, 3), (5, 3), (5, 5), (3, 5)]]),
    Polygon([(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)]),
])
def test_holes_and_concave_inner_never_cross_outside(shape, config, assert_edges_safe):
    plan = make_plan(inner=shape)
    terminals = {"H1": (1, 1), "N1": (5.5, 1)}
    result = build(plan, config, terminals)
    assert_edges_safe(result, plan, config)


def test_same_room_fast_path_does_not_shortcut_across_concave_void(config, assert_edges_safe):
    inner = Polygon([(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)])
    plan = make_plan(inner=inner)
    start, goal = (1, 1), (5, 1)
    result = build(plan, config, {"H1": start, "N1": goal})
    assert result.status == "ready"
    assert not result.graph.has_edge(start, goal)
    assert_edges_safe(result, plan, config)


def test_fallback_restores_connectivity_and_keeps_exact_lines(assert_edges_safe):
    inner = Polygon([(0, 0), (2, 0), (2, 4), (4, 4), (4, 0), (6, 0), (6, 6), (0, 6)])
    plan = make_plan(inner=inner)
    terminals = {"H1": (1.123, 1.321), "N1": (5.321, 1.321)}
    for step in (1, 0.5):
        config = make_config(grid_step_m=step)
        result = build(plan, config, terminals)
        assert result.fallback_used
        assert [a.connected for a in result.attempts] == [False, True]
        assert result.terminal_vertices == terminals
        assert (step, 5) in result.graph
        assert ((0.5, 5) in result.graph) == (step == 0.5)
        assert_edges_safe(result, plan, config)


def test_disconnected_components_are_only_grid_failure(config):
    inner = MultiPolygon([box(0, 0, 2, 2), box(4, 0, 6, 2)])
    plan = make_plan(inner=inner)
    result = build(plan, config, {"H1": (1, 1), "N1": (5, 1)})
    assert result.status == "unreachable_on_grid"
    assert result.graph is None
    assert len(result.attempts) == 4
    assert "not proven geometric infeasibility" in result.diagnostics[0]


def test_margin_failure_is_not_geometric_infeasibility():
    plan = make_plan(inner=box(0, 0, 4, 2), walls=box(0, 0, 4, 0.99), windows=box(0, 1.01, 4, 2))
    terminals = {"H1": (0.5, 1), "N1": (3.5, 1)}
    blocked = build(plan, make_config(cable_wall_margin_m=0.02), terminals)
    assert blocked.status == "unreachable_on_grid"
    assert "not proven geometric infeasibility" in blocked.diagnostics[0]
    assert build(plan, make_config(cable_wall_margin_m=0.001), terminals).status == "ready"


def test_coincident_terminal_inside_obstacle_does_not_succeed(config):
    plan = make_plan(walls=box(4, 4, 6, 6))
    result = build(plan, config, {"H1": (5, 5), "N1": (5, 5)})
    assert result.status == "unreachable_on_grid"


@pytest.mark.parametrize("override", [
    {"grid_step_m": 0}, {"grid_step_m": -1}, {"grid_step_m": float("nan")},
    {"cable_wall_margin_m": -1}, {"tolerance_m": float("inf")}, {"cable_transit_policy": "guess"},
])
def test_bad_config_raises_value_error(plan, terminals, override):
    with pytest.raises(ValueError):
        build(plan, make_config(**override), terminals)


def test_bad_terminals_and_missing_contract_fields(plan, config):
    with pytest.raises(ValueError, match="finite"):
        build(plan, config, {"H1": (float("nan"), 1), "N1": (2, 2)})
    with pytest.raises(ValueError, match="start_id"):
        build(plan, config, {"N1": (2, 2)})
    with pytest.raises(ValueError, match="BACKEND_SCHEMA"):
        build(SimpleNamespace(), config, {"H1": (1, 1), "N1": (2, 2)})
    with pytest.raises(ValueError, match="IDs must be strings"):
        build(plan, config, {"H1": (1, 1), 123: (2, 2)})


def test_invalid_polygon_is_reported_without_repair(config):
    invalid = Polygon([(0, 0), (4, 4), (0, 4), (4, 0), (0, 0)])
    plan = make_plan()
    plan.inner_m = invalid
    before = invalid.wkb
    result = build(plan, config, {"H1": (1, 1), "N1": (2, 2)})
    assert result.status == "unsupported_geometry"
    assert "no repair" in result.diagnostics[0]
    assert plan.inner_m.wkb == before


def test_overlapping_and_boundary_room_ownership_are_unsupported(config):
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 6, 10)), make_room("R2", box(5, 0, 10, 10))])
    result = build(plan, config, {"H1": (1, 1), "N1": (9, 1)})
    assert result.status == "unsupported_geometry"
    assert "Overlapping" in result.diagnostics[0]
    plan = make_plan()
    result = build(plan, config, {"H1": (0, 1), "N1": (9, 1)})
    assert result.status == "unsupported_geometry"
    assert "boundary" in result.diagnostics[0]
