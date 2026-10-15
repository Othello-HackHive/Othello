"""T07: every rule passes on good plans and fails on the right broken plans."""

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker.rules import (
    RULES,
    check_all,
    check_capacity,
    check_coverage,
    check_doors,
    check_free_space,
    check_hub_points,
    check_node_positions,
    check_rings,
    check_schema,
    check_zones,
    room_changes,
    run_rules,
)
from othello.models.config import SolverConfig
from othello.models.result import VIOLATION_CODES

CFG = SolverConfig()


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


@pytest.fixture(scope="module")
def tiny():
    return tiny_plan()


def codes(violations):
    return {v.type for v in violations}


GOOD = [designs.slide5_design, designs.slide5_with_crossing, designs.slide5_with_overlap]

# the main rule each broken plan is built to break
EXPECTED = {
    "missing_node": {"RING_NODE_MISSING"},
    "ring_ends_at_other_hub": {"RING_WRONG_HUB"},
    "capacity_all_in_one_hub": {"CAPACITY_NODES", "CAPACITY_AREA"},
    "hub_in_wall": {"HUB_WALL_CLEARANCE"},
    "hub_too_close_to_wall": {"HUB_WALL_CLEARANCE"},
    "cable_through_window": {"WINDOW_INTERSECTION"},
    "cable_through_wall": {"WALL_INTERSECTION", "ROOM_TRANSITION_NOT_VIA_OPENING"},
    "diagonal_segment": {"SEGMENT_NOT_AXIS_ALIGNED"},
    "cable_outside_inner": {"OUTSIDE_INNER"},
    "territory_disconnected": {"ZONE_DISCONNECTED"},
    "coverage_missing": {"COVERAGE_MISSING"},
    "coverage_duplicate": {"COVERAGE_DUPLICATE"},
    "node_moved": {"NODE_MOVED"},
}


# ------------------------------------------------------- the big picture


@pytest.mark.parametrize("build", GOOD)
def test_good_plans_break_no_rule_even_with_crossings_and_overlap(plan, build):
    assert check_all(plan, build(), CFG) == []


def test_good_tiny_plan_breaks_no_rule(tiny):
    assert check_all(tiny, designs.tiny_design(), CFG) == []


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_each_broken_plan_is_caught_for_its_reason(plan, name):
    found = codes(check_all(plan, designs.ALL_BROKEN[name](), CFG))
    assert EXPECTED[name] <= found, f"{name}: wanted {EXPECTED[name]}, got {found}"


def test_every_broken_plan_has_an_expected_reason():
    assert set(EXPECTED) == set(designs.ALL_BROKEN)


def test_only_official_violation_codes_are_ever_used(plan):
    seen = set()
    for build in designs.ALL_BROKEN.values():
        seen |= codes(check_all(plan, build(), CFG))
    assert seen <= set(VIOLATION_CODES)


def test_run_rules_gives_one_entry_per_rule(plan):
    result = run_rules(plan, designs.slide5_design(), CFG)
    assert list(result) == [rule.rule_id for rule in RULES]
    assert all(violations == [] for violations in result.values())


def test_crossings_and_overlaps_are_never_violation_codes():
    assert not any("CROSS" in c or "OVERLAP" in c for c in VIOLATION_CODES)


# ------------------------------------------------------------- schema


def test_schema_ok_on_good_plan(plan):
    assert check_schema(plan, designs.slide5_design(), CFG) == []


def test_schema_catches_wrong_plan_id_unknown_ids_and_duplicate_hubs(plan):
    design = designs.slide5_design()
    design.plan_id = "SOMETHING_ELSE"
    design.hubs[0].assigned_rooms.append("R99")
    design.hubs[1].hub_id = "H1"
    found = check_schema(plan, design, CFG)
    assert codes(found) == {"OUTPUT_SCHEMA"}
    details = " ".join(v.detail for v in found)
    assert "SOMETHING_ELSE" in details and "R99" in details and "H1 used 2 times" in details


def test_schema_catches_an_excluded_room_assigned_to_a_hub(plan):
    design = designs.slide5_design()
    design.hubs[1].assigned_rooms.append("R07")  # the bathroom
    assert any("excluded" in v.detail for v in check_schema(plan, design, CFG))


def test_schema_catches_nodes_that_do_not_match_rooms(plan):
    design = designs.slide5_design()
    design.hubs[0].assigned_nodes = ["N5", "N1"]  # N4 left out of the list
    assert any("do not match" in v.detail for v in check_schema(plan, design, CFG))


# ----------------------------------------------------------- H1 coverage


def test_coverage_passes_when_each_room_and_node_has_one_owner(plan):
    assert check_coverage(plan, designs.slide5_design(), CFG) == []


def test_coverage_names_the_missing_room_and_node(plan):
    found = check_coverage(plan, designs.broken_coverage_missing(), CFG)
    assert codes(found) == {"COVERAGE_MISSING"}
    assert [v.detail for v in found] == ["room R06 belongs to no hub", "node N6 belongs to no hub"]


def test_coverage_names_the_duplicated_room_and_node(plan):
    found = check_coverage(plan, designs.broken_coverage_duplicate(), CFG)
    assert codes(found) == {"COVERAGE_DUPLICATE"}
    assert all("H1" in v.detail and "H2" in v.detail for v in found)


# ----------------------------------------------------------- H2 capacity


def test_capacity_passes_on_good_plan(plan):
    assert check_capacity(plan, designs.slide5_design(), CFG) == []


def test_capacity_catches_six_nodes_and_too_much_area(plan):
    found = check_capacity(plan, designs.broken_capacity_all_in_one_hub(), CFG)
    assert codes(found) == {"CAPACITY_NODES", "CAPACITY_AREA"}
    assert all(v.hub_id == "H1" for v in found)


def test_exactly_five_nodes_is_allowed(plan):
    design = designs.broken_capacity_all_in_one_hub()
    design.hubs[0].assigned_rooms = ["R01", "R02", "R03", "R04", "R05"]
    design.hubs[0].assigned_nodes = ["N1", "N2", "N3", "N4", "N5"]
    assert "CAPACITY_NODES" not in codes(check_capacity(plan, design, CFG))


def test_capacity_limits_come_from_the_config(plan):
    design = designs.slide5_design()  # H1: 3 nodes, 71 m2
    assert check_capacity(plan, design, SolverConfig(max_nodes_per_hub=2)) != []
    assert codes(check_capacity(plan, design, SolverConfig(max_area_per_hub_m2=70.0))) == {"CAPACITY_AREA"}
    assert check_capacity(plan, design, SolverConfig(max_area_per_hub_m2=71.0)) == []


# ------------------------------------------------------------- H3 zones


def test_zones_pass_when_rooms_are_joined_by_doors(plan):
    assert check_zones(plan, designs.slide5_design(), CFG) == []


def test_zones_catch_a_room_that_only_touches_another_hubs_room(plan):
    found = check_zones(plan, designs.broken_territory_disconnected(), CFG)
    assert [v.type for v in found] == ["ZONE_DISCONNECTED"]
    assert found[0].hub_id == "H2" and "R04" in found[0].detail


def test_a_one_room_hub_is_trivially_connected(plan):
    design = designs.slide5_design()
    design.hubs[0].assigned_rooms = ["R05"]
    design.hubs[0].assigned_nodes = ["N5"]
    assert check_zones(plan, design, CFG) == []


def test_the_front_entrance_does_not_join_rooms(plan):
    assert plan.room_graph["R05"].count("R05") == 0
    assert plan.openings["E1"].counts_for_zone is False


# ----------------------------------------------------------- H6 hub spot


def test_hub_points_pass_on_good_plan(plan):
    assert check_hub_points(plan, designs.slide5_design(), CFG) == []


def test_hub_in_a_wall_and_hub_too_close_are_both_clearance_problems(plan):
    assert codes(check_hub_points(plan, designs.broken_hub_in_wall(), CFG)) == {"HUB_WALL_CLEARANCE"}
    close = check_hub_points(plan, designs.broken_hub_too_close_to_wall(), CFG)
    assert codes(close) == {"HUB_WALL_CLEARANCE"}
    assert close[0].point == pytest.approx((3.3, 3.5))


def test_hub_exactly_half_a_metre_from_the_wall_passes(plan):
    design = designs.slide5_design()
    design.hubs[0].location = (3.55, 3.5)
    assert check_hub_points(plan, design, CFG) == []


def test_hub_outside_its_room_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[0].room_id = "R01"  # location (5, 3.5) is in the living room, not bedroom 1
    assert "HUB_NOT_IN_ROOM" in codes(check_hub_points(plan, design, CFG))


def test_hub_in_a_room_it_does_not_own_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[1].room_id = "R05"  # H2's rooms are R03, R02, R06
    found = check_hub_points(plan, design, CFG)
    assert "HUB_NOT_IN_ROOM" in codes(found)


def test_hub_in_the_bathroom_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[1].room_id = "R07"
    design.hubs[1].location = (13.0, 7.0)
    found = check_hub_points(plan, design, CFG)
    assert codes(found) == {"HUB_NOT_IN_ROOM"}


def test_clearance_distance_comes_from_the_config(plan):
    design = designs.slide5_design()  # H1 at (5, 3.5): 1.45 m from the nearest wall
    assert check_hub_points(plan, design, SolverConfig(hub_wall_clearance_m=2.0)) != []


# ------------------------------------------------------------- H4 rings


def test_rings_pass_on_good_plan(plan):
    assert check_rings(plan, designs.slide5_design(), CFG) == []


def test_ring_that_skips_a_node_is_caught(plan):
    found = check_rings(plan, designs.broken_missing_node(), CFG)
    assert codes(found) == {"RING_NODE_MISSING"}
    assert "N4" in found[0].detail


def test_ring_that_ends_at_another_hub_is_caught(plan):
    assert codes(check_rings(plan, designs.broken_ring_ends_at_other_hub(), CFG)) == {"RING_WRONG_HUB"}


def test_ring_that_ends_somewhere_else_is_not_closed(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.sequence[-1] = "N1"
    assert "RING_NOT_CLOSED" in codes(check_rings(plan, design, CFG))


def test_ring_that_visits_a_node_twice_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.sequence = ["H1", "N5", "N4", "N1", "N4", "H1"]
    assert "RING_NODE_REPEATED" in codes(check_rings(plan, design, CFG))


def test_ring_that_visits_a_node_it_does_not_own_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.sequence = ["H1", "N5", "N4", "N1", "N3", "H1"]
    assert "RING_BRANCH" in codes(check_rings(plan, design, CFG))


def test_ring_using_a_hub_as_a_stop_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.sequence = ["H1", "N5", "H2", "N1", "H1"]
    assert "RING_WRONG_HUB" in codes(check_rings(plan, design, CFG))


def test_cable_that_jumps_between_legs_is_a_broken_chain(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.legs[1].polyline[0] = (7.0, 2.6)  # does not start where the last leg ended
    found = check_rings(plan, design, CFG)
    assert "RING_BRANCH" in codes(found)


def test_wrong_number_of_legs_is_caught(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.legs.pop()
    assert "RING_BRANCH" in codes(check_rings(plan, design, CFG))


def test_cable_that_does_not_start_at_the_hub_is_open_ended(plan):
    design = designs.slide5_design()
    design.hubs[0].ring.legs[0].polyline[0] = (5.0, 3.0)
    assert "RING_NOT_CLOSED" in codes(check_rings(plan, design, CFG))


# --------------------------------------------------------------- nodes


def test_nodes_pass_on_good_plan(plan):
    assert check_node_positions(plan, designs.slide5_design(), CFG) == []


def test_moved_node_is_caught_with_its_spot(plan):
    found = check_node_positions(plan, designs.broken_node_moved(), CFG)
    assert codes(found) == {"NODE_MOVED"}
    assert found[0].point == pytest.approx((2.3, 7.0))


def test_a_node_within_one_millimetre_is_fine(plan):
    design = designs.slide5_design()
    leg = design.hubs[0].ring.legs[2]
    leg.polyline[-1] = (2.0005, 7.0)
    design.hubs[0].ring.legs[3].polyline[0] = (2.0005, 7.0)
    assert check_node_positions(plan, design, CFG) == []


# ---------------------------------------------------------- free space


def test_free_space_passes_on_good_plan(plan):
    assert check_free_space(plan, designs.slide5_design(), CFG) == []


def test_free_space_reports_leg_number_and_spot(plan):
    found = check_free_space(plan, designs.broken_cable_through_wall(), CFG)
    assert codes(found) == {"WALL_INTERSECTION"}
    assert found[0].hub_id == "H1" and found[0].ring_id == "H1" and found[0].segment_index == 2
    assert found[0].point is not None


def test_each_free_space_problem_has_its_own_code(plan):
    assert codes(check_free_space(plan, designs.broken_diagonal_segment(), CFG)) == {"SEGMENT_NOT_AXIS_ALIGNED"}
    assert "WINDOW_INTERSECTION" in codes(check_free_space(plan, designs.broken_cable_through_window(), CFG))
    assert "OUTSIDE_INNER" in codes(check_free_space(plan, designs.broken_cable_outside_inner(), CFG))


# ---------------------------------------------------------------- doors


def test_doors_pass_on_good_plans(plan):
    for build in GOOD:
        assert check_doors(plan, build(), CFG) == []


def test_room_changes_found_for_a_cable_through_a_door(plan):
    # from the living room into bedroom 1 through the door D1 (y = 5, x = 3.05..3.95)
    changes = room_changes(plan, [(3.5, 3.0), (3.5, 7.0)])
    assert [(a, b) for a, b, _ in changes] == [("R05", "R01")]
    assert changes[0][2][1] == pytest.approx(5.0, abs=0.06)


def test_no_room_change_when_staying_inside_one_room(plan):
    assert room_changes(plan, [(4.0, 1.0), (9.0, 1.0)]) == []


def test_cable_through_a_wall_is_not_via_a_door(plan):
    found = check_doors(plan, designs.broken_cable_through_wall(), CFG)
    assert codes(found) == {"ROOM_TRANSITION_NOT_VIA_OPENING"}
    assert "R04 -> R01" in found[0].detail


def test_crossing_between_rooms_away_from_every_door_is_caught(plan):
    design = designs.slide5_design()
    # Leg 3 normally goes through doors D4 and D1.  This version walks into bedroom 3
    # through the plain wall at x = 3 (the door D4 is at y = 1.0..1.9, not y = 4.0)
    # and into bedroom 1 through the plain wall at y = 5 (no door between them).
    design.hubs[0].ring.legs[2].polyline = [
        (1.5, 2.5),
        (1.5, 1.7),
        (3.3, 1.7),
        (3.3, 4.0),
        (0.5, 4.0),
        (0.5, 7.0),
        (2.0, 7.0),
    ]
    found = check_doors(plan, design, CFG)
    assert codes(found) == {"ROOM_TRANSITION_NOT_VIA_OPENING"}
    assert len(found) >= 2


def test_cable_leaving_the_house_is_not_a_room_change(plan):
    found = check_doors(plan, designs.broken_cable_through_window(), CFG)
    assert found == []  # it is caught by the window and outside rules instead
