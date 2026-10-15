"""T04: scorekeeper (L, X, O, B, J). Expected numbers are worked out by hand."""

import pytest

from othello.checker.metrics import (
    area_gap,
    clean_polyline,
    compute_metrics,
    count_bends_closed,
    count_crossings,
    is_better,
    overlap_length,
    polyline_length,
    total_cost,
)
from othello.models.config import SolverConfig, Weights
from othello.models.design import Design, Hub, Ring, RouteLeg

TOL = 0.001


def make_hub(hub_id, location, legs, nodes=("N1",), room="R01"):
    """legs: list of (from_id, to_id, polyline)."""
    ring_legs = [RouteLeg(from_id=a, to_id=b, polyline=p) for a, b, p in legs]
    sequence = [ring_legs[0].from_id] + [leg.to_id for leg in ring_legs]
    ring = Ring(hub_id=hub_id, sequence=sequence, legs=ring_legs)
    return Hub(
        hub_id=hub_id,
        room_id=room,
        location=location,
        assigned_rooms=[room],
        assigned_nodes=list(nodes),
        ring=ring,
    )


# --------------------------------------------------- slide 5: the J formula


def test_slide5_costs():
    assert total_cost(23.8, 0, 0.0, 8) == pytest.approx(27.8)  # H1: 23.8 + 0.5*8
    assert total_cost(25.2, 0, 0.0, 12) == pytest.approx(31.2)  # H2: 25.2 + 0.5*12
    assert total_cost(23.8 + 25.2, 0, 0.0, 20) == pytest.approx(59.0)


def test_cost_uses_the_official_weights():
    assert total_cost(10, 1, 2, 4) == pytest.approx(10 + 5 + 2 + 2)


def test_cost_weights_are_configurable():
    w = Weights(length=2.0, crossing=10.0, overlap=3.0, bend=1.0)
    assert total_cost(10, 1, 2, 4, w) == pytest.approx(20 + 10 + 6 + 4)


# ------------------------------------------------------- slide 7: length


def test_length_counts_every_leg_including_return():
    legs = [[(0, 0), (4, 0)], [(4, 0), (4, 2)], [(4, 2), (1, 2)], [(1, 2), (1, 0), (0, 0)]]
    assert sum(polyline_length(leg) for leg in legs) == pytest.approx(4 + 2 + 3 + 3)  # 12 m


def test_retraced_stretch_counts_each_time():
    out_and_back = [[(0, 0), (3, 0)], [(3, 0), (0, 0)]]
    assert sum(polyline_length(leg) for leg in out_and_back) == pytest.approx(6.0)


# --------------------------------------------------------- slide 7: bends


def test_straight_ring_part_has_no_bend():
    pts = [(0, 0), (2, 0), (5, 0)]
    cleaned = clean_polyline(pts, TOL)
    assert cleaned == [(0, 0), (5, 0)]


def test_rectangle_ring_has_four_bends():
    ring = [(0, 0), (4, 0), (4, 3), (0, 3), (0, 0)]
    assert count_bends_closed(ring, TOL) == 4


def test_staircase_ring_has_six_bends():
    # east, north, east, north, west, south, then the closing turn back to east
    ring = [(0, 0), (2, 0), (2, 1), (4, 1), (4, 3), (0, 3), (0, 0)]
    assert count_bends_closed(ring, TOL) == 6


def test_a_path_with_two_turns_keeps_exactly_two_corners():
    cleaned = clean_polyline([(0, 0), (2, 0), (2, 1), (4, 1)], TOL)
    assert cleaned == [(0, 0), (2, 0), (2, 1), (4, 1)]  # two inner corners = two bends


def test_reversal_counts_once_and_hub_closing_turn_counts():
    out_and_back = [(0, 0), (3, 0), (0, 0)]
    # reversal at the node = 1, closing turn back at the hub = 1
    assert count_bends_closed(out_and_back, TOL) == 2


def test_straight_through_a_node_is_not_a_bend():
    # leg 1 ends at the node going east, leg 2 continues east: no turn
    ring = [(0, 0), (3, 0), (3, 0), (6, 0), (6, 2), (0, 2), (0, 0)]
    assert count_bends_closed(ring, TOL) == 4


# ----------------------------------------------------- slide 7: crossings


def test_cables_meeting_in_the_open_is_one_crossing():
    legs = [[(0, 2), (4, 2)], [(2, 0), (2, 4)]]
    anchors = [(0, 2), (4, 2), (2, 0), (2, 4)]
    assert count_crossings(legs, anchors, TOL) == 1


def test_legs_meeting_at_a_node_is_not_a_crossing():
    legs = [[(0, 0), (3, 0)], [(3, 0), (3, 3)]]
    assert count_crossings(legs, anchors=[(0, 0), (3, 0), (3, 3)], tol=TOL) == 0


def test_three_cables_through_one_point_count_once():
    legs = [[(0, 2), (4, 2)], [(2, 0), (2, 4)], [(2, 1), (2, 3)]]
    anchors = [(0, 2), (4, 2), (2, 0), (2, 4), (2, 1), (2, 3)]
    assert count_crossings(legs, anchors, TOL) == 1


def test_a_cable_bending_at_its_own_corner_is_not_a_crossing():
    legs = [[(0, 0), (3, 0), (3, 3)]]
    assert count_crossings(legs, anchors=[(0, 0), (3, 3)], tol=TOL) == 0


def test_t_touch_counts_by_default_and_can_be_switched_off():
    # one cable ends in the middle of another (a T)
    legs = [[(0, 0), (4, 0)], [(2, 0), (2, 3)]]
    anchors = [(0, 0), (4, 0), (2, 3)]
    assert count_crossings(legs, anchors, TOL, t_touch_counts=True) == 1
    assert count_crossings(legs, anchors, TOL, t_touch_counts=False) == 0


def test_parallel_cables_never_cross():
    legs = [[(0, 0), (4, 0)], [(0, 1), (4, 1)]]
    assert count_crossings(legs, anchors=[], tol=TOL) == 0


def test_collinear_overlap_adds_no_crossing_by_default():
    legs = [[(0, 0), (4, 0)], [(1, 0), (3, 0)]]
    assert count_crossings(legs, anchors=[(0, 0), (4, 0)], tol=TOL) == 0
    assert count_crossings(legs, anchors=[(0, 0), (4, 0)], tol=TOL, overlap_endpoint_counts=True) == 2


def test_crossing_at_a_node_of_another_ring_is_excluded():
    legs = [[(0, 2), (4, 2)], [(2, 0), (2, 4)]]
    assert count_crossings(legs, anchors=[(2, 2)], tol=TOL) == 0


# ------------------------------------------------------ slide 7: overlap


def test_two_metres_on_the_same_line_is_overlap_two():
    legs = [[(0, 0), (4, 0)], [(2, 0), (6, 0)]]  # shared stretch x = 2..4
    assert overlap_length(legs, TOL) == pytest.approx(2.0)


def test_parallel_runs_on_different_lines_do_not_overlap():
    legs = [[(0, 0), (4, 0)], [(0, 0.5), (4, 0.5)]]
    assert overlap_length(legs, TOL) == 0.0


def test_lines_within_one_millimetre_are_the_same_line():
    assert overlap_length([[(0, 0), (4, 0)], [(0, 0.0005), (4, 0.0005)]], TOL) == pytest.approx(4.0)
    assert overlap_length([[(0, 0), (4, 0)], [(0, 0.002), (4, 0.002)]], TOL) == 0.0


def test_a_stretch_covered_by_three_legs_counts_twice():
    legs = [[(0, 0), (5, 0)], [(0, 0), (5, 0)], [(0, 0), (5, 0)]]
    assert overlap_length(legs, TOL) == pytest.approx(10.0)  # (3 - 1) * 5


def test_end_to_end_cables_do_not_overlap():
    legs = [[(0, 0), (3, 0)], [(3, 0), (6, 0)]]
    assert overlap_length(legs, TOL) == 0.0


def test_vertical_overlap_works_too():
    legs = [[(1, 0), (1, 5)], [(1, 3), (1, 8)]]
    assert overlap_length(legs, TOL) == pytest.approx(2.0)


# ----------------------------------------------- whole designs, by hand


def test_out_and_back_design():
    hub = make_hub("H1", (0, 0), [("H1", "N1", [(0, 0), (3, 0)]), ("N1", "H1", [(3, 0), (0, 0)])])
    m = compute_metrics(Design(plan_id="P", hubs=[hub]))
    assert m.hub_count == 1
    assert m.L == pytest.approx(6.0)
    assert m.X == 0
    assert m.O == pytest.approx(3.0)  # the same 3 m covered twice
    assert m.B == 2  # reversal at the node + closing turn at the hub
    assert m.J == pytest.approx(6 + 0 + 3 + 0.5 * 2)  # 10


def test_two_hub_design_with_crossings_and_overlap():
    ring_a = make_hub(
        "H1",
        (0, 0),
        [
            ("H1", "N1", [(0, 0), (4, 0)]),
            ("N1", "N2", [(4, 0), (4, 3)]),
            ("N2", "H1", [(4, 3), (0, 3), (0, 0)]),
        ],
        nodes=("N1", "N2"),
    )
    ring_b = make_hub(
        "H2",
        (2, -1),
        [("H2", "N3", [(2, -1), (2, 5)]), ("N3", "H2", [(2, 5), (2, -1)])],
        nodes=("N3",),
        room="R02",
    )
    m = compute_metrics(Design(plan_id="P", hubs=[ring_a, ring_b]), hub_areas={"H1": 71.0, "H2": 47.0})
    assert m.hub_count == 2
    assert m.L == pytest.approx(14 + 12)
    assert m.X == 2  # ring B crosses ring A at (2,0) and (2,3)
    assert m.O == pytest.approx(6.0)  # ring B retraces its 6 m
    assert m.B == 4 + 2
    assert m.J == pytest.approx(26 + 5 * 2 + 6 + 0.5 * 6)  # 45
    assert m.area_gap_m2 == pytest.approx(24.0)
    assert m.per_hub["H1"]["ring_length_m"] == pytest.approx(14.0)
    assert m.per_hub["H1"]["bends"] == 4
    assert m.per_hub["H2"]["area_m2"] == 47.0


def test_changing_config_changes_the_score_but_not_the_counts():
    hub = make_hub("H1", (0, 0), [("H1", "N1", [(0, 0), (3, 0)]), ("N1", "H1", [(3, 0), (0, 0)])])
    cfg = SolverConfig(weights=Weights(length=1.0, crossing=5.0, overlap=2.0, bend=1.0))
    m = compute_metrics(Design(plan_id="P", hubs=[hub]), cfg)
    assert (m.L, m.X, m.O, m.B) == (pytest.approx(6.0), 0, pytest.approx(3.0), 2)
    assert m.J == pytest.approx(6 + 0 + 2 * 3 + 1 * 2)


def test_empty_design_scores_zero():
    m = compute_metrics(Design(plan_id="P", hubs=[]))
    assert (m.hub_count, m.L, m.X, m.O, m.B, m.J) == (0, 0.0, 0, 0.0, 0, 0.0)


# ------------------------------------------------------------- ranking


def test_area_gap_is_largest_minus_smallest():
    assert area_gap({"H1": 71.0, "H2": 47.0}) == pytest.approx(24.0)
    assert area_gap({}) == 0.0


def test_fewer_hubs_beats_lower_cost():
    from othello.models.result import Metrics

    two = Metrics(hub_count=2, L=10, X=0, O=0.0, B=0, J=10.0)
    one = Metrics(hub_count=1, L=500, X=9, O=9.0, B=99, J=999.0)
    assert is_better(one, two)
    assert not is_better(two, one)


def test_equal_hubs_lower_cost_wins_then_smaller_gap():
    from othello.models.result import Metrics

    a = Metrics(hub_count=2, L=10, X=0, O=0.0, B=0, J=27.8, area_gap_m2=24.0)
    b = Metrics(hub_count=2, L=10, X=0, O=0.0, B=0, J=31.2, area_gap_m2=1.0)
    assert is_better(a, b)
    c = Metrics(hub_count=2, L=10, X=0, O=0.0, B=0, J=27.8, area_gap_m2=5.0)
    assert is_better(c, a)  # same J: smaller gap wins
