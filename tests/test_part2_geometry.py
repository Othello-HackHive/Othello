"""T06: the teacher's measuring tools."""

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan
from shapely.geometry import Point

from othello.checker.geometry import (
    NOT_AXIS_ALIGNED,
    OUTSIDE,
    WALL_HIT,
    WINDOW_HIT,
    FreeSpaceChecker,
    almost_equal,
    distance_to,
    has_clearance,
    is_axis_aligned,
    merge_collinear,
    non_axis_segments,
    point_in_polygon,
)

TOL = 0.001


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


@pytest.fixture(scope="module")
def tiny():
    return tiny_plan()


def kinds_in(checker, design):
    found = set()
    for hub in design.hubs:
        for leg in hub.ring.legs:
            found |= {issue.kind for issue in checker.issues(leg.polyline)}
    return found


# ------------------------------------------------------- points and lines


def test_almost_equal_uses_one_millimetre():
    assert almost_equal((0, 0), (0.0009, 0.0009), TOL)
    assert almost_equal((1, 1), (1, 1), TOL)
    assert not almost_equal((0, 0), (0.002, 0), TOL)
    assert not almost_equal((0, 0), (0, 0.002), TOL)


def test_axis_aligned_pieces():
    assert is_axis_aligned((0, 0), (5, 0))
    assert is_axis_aligned((2, 1), (2, 9))
    assert is_axis_aligned((0, 0), (5, 0.0005))  # a hair off, still horizontal
    assert not is_axis_aligned((0, 0), (5, 1))


def test_non_axis_segments_lists_the_slanted_ones():
    assert non_axis_segments([(0, 0), (3, 0), (4, 1), (4, 5)]) == [1]
    assert non_axis_segments([(0, 0), (3, 0), (3, 4)]) == []


def test_merge_collinear_joins_straight_stretches_and_keeps_reversals():
    assert merge_collinear([(0, 0), (2, 0), (5, 0)]) == [(0, 0), (5, 0)]
    assert merge_collinear([(0, 0), (3, 0), (0, 0)]) == [(0, 0), (3, 0), (0, 0)]
    assert merge_collinear([(0, 0), (0, 0), (1, 0)]) == [(0, 0), (1, 0)]


# ------------------------------------------------------- rooms and walls


def test_point_in_polygon_allows_the_tolerance(tiny):
    room = tiny.rooms["R01"].polygon_m  # 0..4 x 0..4
    assert point_in_polygon((2, 2), room)
    assert point_in_polygon((4, 2), room)  # on the edge
    assert point_in_polygon((4.0005, 2), room)  # within 1 mm
    assert not point_in_polygon((4.002, 2), room)
    assert not point_in_polygon((9, 9), room)


def test_distance_to_a_wall(tiny):
    assert distance_to((2, 2), tiny.walls_m) == pytest.approx(1.95, abs=1e-6)  # wall face at 0.05 and 3.95


def test_clearance_half_a_metre(plan):
    assert has_clearance((7.0, 2.5), plan.walls_m)  # far from walls
    assert not has_clearance((3.3, 3.5), plan.walls_m)  # only 0.25 m from the wall face
    assert has_clearance((3.55, 3.5), plan.walls_m)  # exactly 0.5 m from the wall face


# ----------------------------------------------- cables vs the real shapes


@pytest.mark.parametrize(
    "build",
    [designs.slide5_design, designs.slide5_with_crossing, designs.slide5_with_overlap],
)
def test_good_slide5_cables_have_no_problems(plan, build):
    checker = FreeSpaceChecker.from_plan(plan)
    for hub in build().hubs:
        for leg in hub.ring.legs:
            assert checker.issues(leg.polyline) == [], f"{hub.hub_id} {leg.from_id}->{leg.to_id}"


def test_good_tiny_cables_have_no_problems(tiny):
    checker = FreeSpaceChecker.from_plan(tiny)
    for leg in designs.tiny_design().hubs[0].ring.legs:
        assert checker.is_clear(leg.polyline)


def test_cable_through_a_wall_is_caught_as_wall_only(plan):
    kinds = kinds_in(FreeSpaceChecker.from_plan(plan), designs.broken_cable_through_wall())
    assert kinds == {WALL_HIT}


def test_cable_through_a_window_is_caught(plan):
    kinds = kinds_in(FreeSpaceChecker.from_plan(plan), designs.broken_cable_through_window())
    assert WINDOW_HIT in kinds
    assert OUTSIDE in kinds  # it also leaves the house


def test_slanted_cable_is_caught(plan):
    kinds = kinds_in(FreeSpaceChecker.from_plan(plan), designs.broken_diagonal_segment())
    assert kinds == {NOT_AXIS_ALIGNED}


def test_cable_leaving_the_house_is_caught(plan):
    kinds = kinds_in(FreeSpaceChecker.from_plan(plan), designs.broken_cable_outside_inner())
    assert OUTSIDE in kinds
    assert NOT_AXIS_ALIGNED not in kinds


def test_issue_reports_where_it_happened(plan):
    checker = FreeSpaceChecker.from_plan(plan)
    polyline = designs.broken_cable_through_wall().hubs[0].ring.legs[2].polyline
    issues = checker.issues(polyline)
    assert len(issues) == 1
    issue = issues[0]
    assert issue.kind == WALL_HIT and issue.segment_index == 0
    # the marked spot is on the wall between bedroom 3 and bedroom 1 (y = 5)
    assert issue.point[1] == pytest.approx(5.0, abs=0.06)
    assert Point(issue.point).distance(plan.walls_m) < 1e-6


# ------------------------------------------------- the one millimetre rule


def test_a_cable_going_through_a_door_gap_is_fine(tiny):
    checker = FreeSpaceChecker.from_plan(tiny)
    assert checker.is_clear([(3.0, 2.0), (5.0, 2.0)])


def test_a_cable_through_a_plain_wall_is_not(tiny):
    checker = FreeSpaceChecker.from_plan(tiny)
    kinds = {i.kind for i in checker.issues([(3.0, 3.5), (5.0, 3.5)])}
    assert kinds == {WALL_HIT}


def test_touching_a_wall_surface_is_fine_but_going_into_it_is_not(tiny):
    checker = FreeSpaceChecker.from_plan(tiny)
    # the wall between the rooms is 3.95 .. 4.05 wide
    assert checker.is_clear([(3.95, 0.5), (3.95, 1.0)])  # grazing its face
    assert checker.is_clear([(3.9495, 0.5), (3.9495, 1.0)])  # 0.5 mm in: within tolerance
    kinds = {i.kind for i in checker.issues([(3.99, 0.5), (3.99, 1.0)])}  # 4 cm in
    assert kinds == {WALL_HIT}


def test_zero_length_pieces_are_ignored(tiny):
    checker = FreeSpaceChecker.from_plan(tiny)
    assert checker.is_clear([(1.0, 1.0), (1.0, 1.0), (2.0, 1.0)])


def test_a_polyline_with_one_point_has_no_pieces(tiny):
    assert FreeSpaceChecker.from_plan(tiny).issues([(1.0, 1.0)]) == []
