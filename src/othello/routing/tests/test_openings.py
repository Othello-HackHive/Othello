import networkx as nx
import pytest
from shapely.geometry import LineString, box
from shapely.ops import unary_union

from othello.routing import build_routing_graph
from conftest import make_config, make_opening, make_plan, make_room


def doorway(kind="door", thick=False):
    left, right = (4, 6) if thick else (5, 5)
    walls = unary_union([box(4, 0, 6, 4), box(4, 6, 6, 10)])
    return make_plan(
        rooms=[make_room("R1", box(0, 0, left, 10)), make_room("R2", box(right, 0, 10, 10))],
        walls=walls,
        openings=[make_opening("D1", ["R1", "R2"], [(5, 4), (5, 6)], kind)],
    )


@pytest.mark.parametrize("kind", ["door", "open_passage"])
@pytest.mark.parametrize("thick", [False, True])
def test_explicit_openings_and_thick_wall_gaps(kind, thick, config, assert_edges_safe):
    plan = doorway(kind, thick)
    terminals = {"H1": (1, 5), "N1": (9, 5)}
    result = build_routing_graph(plan, terminals, "H1", "N1", config)
    assert_edges_safe(result, plan, config)
    crossed = [(a, b, d) for a, b, d in result.graph.edges(data=True) if d["opening_ids"]]
    assert crossed
    assert all(d["opening_ids"] == ("D1",) for _, _, d in crossed)
    assert all(x != 5 for x, _ in result.graph)


def test_multiple_crossings_in_order_and_reverse(config, assert_edges_safe, assert_room_transitions_authorized):
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 3, 10)), make_room("R2", box(3, 0, 7, 10)),
                            make_room("R3", box(7, 0, 10, 10))],
                     openings=[make_opening("D1", ["R1", "R2"], [(3, 2), (3, 8)]),
                               make_opening("P1", ["R2", "R3"], [(7, 2), (7, 8)], "open_passage")])
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert_edges_safe(result, plan, config)
    assert_room_transitions_authorized(result.graph, plan, config)
    assert result.graph[(1, 5)][(9, 5)]["opening_ids"] == ("D1", "P1")
    assert result.graph[(9, 5)][(1, 5)]["opening_ids"] == ("P1", "D1")


def test_missing_opening_and_mismatched_rooms_are_diagnostic(config):
    plan = doorway()
    plan.openings = {}
    terminals = {"H1": (1, 5), "N1": (9, 5)}
    result = build_routing_graph(plan, terminals, "H1", "N1", config)
    assert result.status == "unsupported_geometry"
    assert "No opening contract" in result.diagnostics[0]
    assert result.attempts == ()
    plan = doorway()
    plan.openings["D1"].rooms = ["R1", "missing"]
    result = build_routing_graph(plan, terminals, "H1", "N1", config)
    assert result.status == "unsupported_geometry"
    assert "two interior rooms" in result.diagnostics[0]


def test_wall_only_transition_is_blocked(config):
    plan = doorway()
    plan.openings = {}
    plan.walls_m = box(4, 0, 6, 10)
    plan.free_space_m = plan.inner_m.difference(plan.walls_m)
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "unreachable_on_grid"


def test_gap_endpoint_touch_does_not_authorize_transition(config, assert_edges_safe):
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
                     openings=[make_opening("D1", ["R1", "R2"], [(5, 4), (5, 6)])])
    result = build_routing_graph(plan, {"H1": (1, 4), "N1": (9, 4)}, "H1", "N1", config)
    assert_edges_safe(result, plan, config)
    assert not result.graph.has_edge((1, 4), (9, 4))
    assert result.graph.has_edge((1, 5), (9, 5))


@pytest.mark.parametrize("offset", [-0.0005, 0.0005])
@pytest.mark.parametrize("swap_axes", [False, True])
def test_offset_opening_connectivity_is_terminal_insertion_invariant(
        offset, swap_axes, assert_edges_safe, assert_room_transitions_authorized):
    def coord(x, y):
        return (y, x) if swap_axes else (x, y)
    def rectangle(x1, y1, x2, y2):
        return box(y1, x1, y2, x2) if swap_axes else box(x1, y1, x2, y2)
    plan = make_plan(rooms=[make_room("R1", rectangle(0, 0, 5, 10)),
                            make_room("R2", rectangle(5, 0, 10, 10))],
                     openings=[make_opening("P1", ["R1", "R2"],
                                            [coord(5 + offset, 0), coord(5 + offset, 10)], "open_passage")])
    config = make_config(grid_step_m=0.10, cable_transit_policy="forbid")
    original = {"H1": coord(1, 5), "N1": coord(9, 5)}
    for fractions in ((), (0.5,), (0.25, 0.5, 0.75)):
        terminals = {**original, **{f"candidate{i}": coord(5 + offset * f, 8 + i / 4)
                                    for i, f in enumerate(fractions)}}
        result = build_routing_graph(plan, terminals, "H1", "N1", config)
        assert result.status == "ready", (offset, swap_axes, fractions, result.diagnostics)
        assert not result.fallback_used
        assert result.terminal_vertices == terminals
        assert nx.has_path(result.graph, original["H1"], original["N1"])
        assert nx.shortest_path_length(result.graph, original["H1"], original["N1"], weight="length_m") == 8
        assert_edges_safe(result, plan, config)
        assert_room_transitions_authorized(result.graph, plan, config)


@pytest.mark.parametrize("thick", [False, True])
def test_every_room_transition_has_an_authorized_opening_independently(
        thick, config, assert_room_transitions_authorized):
    plan = doorway(thick=thick)
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "ready"
    assert_room_transitions_authorized(result.graph, plan, config)
    # Removing metadata cannot hide an illegal transition from this oracle.
    for reported_ids in ((), ("D1",)):
        corrupted = result.graph.copy()
        corrupted.add_edge((1, 3), (9, 3), opening_ids=reported_ids)
        with pytest.raises(AssertionError, match="authorized opening"):
            assert_room_transitions_authorized(corrupted, plan, config)


@pytest.mark.parametrize("obstacle_kind", ["walls", "windows"])
def test_offset_opening_connections_cannot_bypass_obstacles(obstacle_kind):
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
                     openings=[make_opening("P1", ["R1", "R2"], [(5.0005, 0), (5.0005, 10)], "open_passage")],
                     **{obstacle_kind: box(5.0001, 0, 5.0004, 10)})
    terminals = {"H1": (1, 5), "N1": (9, 5), "candidate": (5.00025, 8)}
    # Zero margin leaves the physical barrier itself as the reason for rejection.
    config = make_config(grid_step_m=0.10, cable_wall_margin_m=0, cable_transit_policy="forbid")
    result = build_routing_graph(plan, terminals, "H1", "N1", config)
    assert result.status == "unreachable_on_grid"


def test_offset_opening_connections_do_not_expand_matching_tolerance():
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
                     openings=[make_opening("P1", ["R1", "R2"], [(5.002, 0), (5.002, 10)], "open_passage")])
    terminals = {"H1": (1, 5), "N1": (9, 5), "candidate": (5.001, 8)}
    config = make_config(grid_step_m=0.10, tolerance_m=0.001, cable_transit_policy="forbid")
    result = build_routing_graph(plan, terminals, "H1", "N1", config)
    assert result.status == "unreachable_on_grid"


def test_collinear_gap_cannot_create_a_boundary_route(config, assert_edges_safe):
    plan = doorway()
    result = build_routing_graph(plan, {"H1": (1, 3), "N1": (9, 7)}, "H1", "N1", config)
    assert_edges_safe(result, plan, config)
    gap = LineString(plan.openings["D1"].gap_polyline_m)
    assert all(LineString([a, b]).intersection(gap).length == 0 for a, b in result.graph.edges)


def test_bent_gap_tangent_does_not_authorize_transition(config):
    gap = [(4.5, 6), (5, 5), (5.5, 6)]
    # The library predicate alone reports True for this tangent contact.
    assert LineString([(1, 5), (9, 5)]).crosses(LineString(gap))
    plan = make_plan(rooms=[make_room("R1", box(0, 0, 5, 10)), make_room("R2", box(5, 0, 10, 10))],
                     openings=[make_opening("D1", ["R1", "R2"], gap)])
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "unreachable_on_grid"


def test_entrance_seeds_coordinates_but_never_opening_metadata(config):
    plan = make_plan(openings=[make_opening("E1", ["R1", "outside"], [(2, 0), (4, 0)], "entrance")])
    result = build_routing_graph(plan, {"H1": (1, 1), "N1": (9, 2)}, "H1", "N1", config)
    assert result.status == "ready"
    assert (3, 1) in result.graph
    assert all(not data["opening_ids"] for _, _, data in result.graph.edges(data=True))


@pytest.mark.parametrize("gap", [
    [(5, 4), (5, 4)],
    [(4.5, 4.5), (5.5, 5.5), (4.5, 5.5), (5.5, 4.5)],
    [(5, 0), (5, 10)],
])
def test_ambiguous_or_obstructed_opening_geometry_is_unsupported(gap, config):
    plan = doorway()
    plan.openings["D1"].gap_polyline_m = gap
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "unsupported_geometry"
    assert result.graph is None
    assert result.diagnostics


def test_duplicate_opening_geometry_is_unsupported(config):
    plan = doorway()
    plan.openings["D2"] = make_opening("D2", ["R1", "R2"], [(5, 4), (5, 6)])
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "unsupported_geometry"
    assert "Ambiguous intersecting" in result.diagnostics[0]


def test_opening_with_wrong_existing_room_pair_is_unsupported(config):
    plan = doorway()
    plan.rooms["R3"] = make_room("R3", box(10, 0, 12, 10))
    plan.openings["D1"].rooms = ["R1", "R3"]
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "unsupported_geometry"
    assert result.graph is None


def test_entrance_never_connects_separate_inner_components(config):
    from shapely.geometry import MultiPolygon

    inner = MultiPolygon([box(0, 0, 2, 2), box(4, 0, 6, 2)])
    plan = make_plan(inner=inner, openings=[make_opening("E1", ["R1", "outside"], [(2, 0), (2, 2)], "entrance")])
    result = build_routing_graph(plan, {"H1": (1, 1), "N1": (5, 1)}, "H1", "N1", config)
    assert result.status == "unreachable_on_grid"


def test_unlabelled_span_without_room_transition_is_unsupported(config):
    room = box(0, 0, 10, 10).difference(box(4, 4, 6, 6))
    plan = make_plan(rooms=[make_room("R1", room)])
    result = build_routing_graph(plan, {"H1": (1, 5), "N1": (9, 5)}, "H1", "N1", config)
    assert result.status == "unsupported_geometry"
    assert "Unlabelled span" in result.diagnostics[0]
