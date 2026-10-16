"""T13: damage good plans in many small ways; the teacher must notice every one.

Also the opposite: harmless changes (and costly-but-legal ones) must stay valid.
"""

import mutations as mut
import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker import check_design
from othello.checker.metrics import is_better
from othello.models.result import VIOLATION_CODES

SLIDE5 = ("slide5", slide5_plan(), designs.slide5_design())
TINY = ("tiny", tiny_plan(), designs.tiny_design())
BOTH = [SLIDE5, TINY]


def _case(cid, plan, design, expected, family):
    return pytest.param(plan, design, set(expected), family, id=f"{cid}:{family}")


def _build_cases():
    cases = []

    for name, plan, good in BOTH:
        for hub in good.hubs:
            for node in hub.assigned_nodes:
                tag = f"{name}:{hub.hub_id}:{node}"
                cases.append(_case(tag, plan, mut.drop_node_from_ring(good, hub.hub_id, node), {"RING_NODE_MISSING"}, "drop_node"))
                cases.append(_case(tag, plan, mut.visit_node_twice(good, hub.hub_id, node), {"RING_NODE_REPEATED"}, "visit_twice"))
                cases.append(_case(tag, plan, mut.shift_node(good, node), {"NODE_MOVED"}, "shift_node"))
            tag = f"{name}:{hub.hub_id}"
            cases.append(_case(tag, plan, mut.end_ring_at_a_node(good, hub.hub_id), {"RING_NOT_CLOSED"}, "end_at_node"))
            cases.append(_case(tag, plan, mut.start_ring_away_from_hub(good, hub.hub_id), {"RING_NOT_CLOSED"}, "start_away"))
            for k in range(len(hub.ring.legs)):
                leg_tag = f"{tag}:leg{k}"
                cases.append(_case(leg_tag, plan, mut.slant_leg(good, hub.hub_id, k), {"SEGMENT_NOT_AXIS_ALIGNED"}, "slant"))
                if k < len(hub.ring.legs) - 1:
                    cases.append(_case(leg_tag, plan, mut.jump_between_legs(good, hub.hub_id, k), {"RING_BRANCH"}, "jump"))
            cases.append(_case(tag, plan, mut.move_hub(good, hub.hub_id, mut.wall_point_of_room(plan, hub.room_id)), {"HUB_WALL_CLEARANCE"}, "hub_in_wall"))
            cases.append(
                _case(tag, plan, mut.move_hub(good, hub.hub_id, mut.near_wall_point_of_room(plan, hub.room_id)), {"HUB_WALL_CLEARANCE"}, "hub_too_close")
            )
        for room in plan.eligible_rooms():
            cases.append(_case(f"{name}:{room.room_id}", plan, mut.drop_room_from_its_hub(good, plan, room.room_id), {"COVERAGE_MISSING"}, "drop_room"))
        # spikes from every hub and node, in every direction
        for kind, code in (("wall", "WALL_INTERSECTION"), ("window", "WINDOW_INTERSECTION"), ("outside", "OUTSIDE_INNER")):
            for hub_id, stop_id, leg_index, direction, length in mut.spike_plans(plan, good)[kind]:
                tag = f"{name}:{hub_id}:{stop_id}:{direction}"
                cases.append(_case(tag, plan, mut.spike(good, hub_id, stop_id, leg_index, direction, length), {code}, f"spike_{kind}"))

    # --- slide 5 only (needs two hubs, or particular rooms)
    _, plan, good = SLIDE5
    cases += [
        _case("H1->H2", plan, mut.end_ring_at_other_hub(good, "H1", "H2"), {"RING_WRONG_HUB"}, "end_other_hub"),
        _case("H2->H1", plan, mut.end_ring_at_other_hub(good, "H2", "H1"), {"RING_WRONG_HUB"}, "end_other_hub"),
        _case("H1", plan, mut.put_hub_in_room(good, "H1", "R01"), {"HUB_NOT_IN_ROOM"}, "hub_wrong_room"),
        _case("H2", plan, mut.put_hub_in_room(good, "H2", "R02"), {"HUB_NOT_IN_ROOM"}, "hub_wrong_room"),
        _case("H1-in-H2s-room", plan, mut.put_hub_in_room(good, "H1", "R03"), {"HUB_NOT_IN_ROOM"}, "hub_foreign_room"),
        _case("H2-in-H1s-room", plan, mut.put_hub_in_room(good, "H2", "R05"), {"HUB_NOT_IN_ROOM"}, "hub_foreign_room"),
        _case("H1-in-bathroom", plan, mut.put_hub_in_room(good, "H1", "R07"), {"HUB_NOT_IN_ROOM"}, "hub_bathroom"),
        _case("all-on-H1", plan, mut.pile_everything_on(good, plan, "H1"), {"CAPACITY_NODES", "CAPACITY_AREA"}, "overload"),
        _case("all-on-H2", plan, mut.pile_everything_on(good, plan, "H2"), {"CAPACITY_NODES", "CAPACITY_AREA"}, "overload"),
        _case("plan-id", plan, mut.wrong_plan_id(good), {"OUTPUT_SCHEMA"}, "schema"),
        _case("unknown-room", plan, mut.unknown_room(good), {"OUTPUT_SCHEMA"}, "schema"),
        _case("duplicate-hub-id", plan, mut.duplicate_hub_id(good), {"OUTPUT_SCHEMA"}, "schema"),
        _case("excluded-room", plan, mut.assign_excluded_room(good, "R07"), {"OUTPUT_SCHEMA"}, "schema"),
    ]
    for room in ("R01", "R02", "R03", "R04", "R05", "R06"):
        other = "H2" if room in ("R05", "R01", "R04") else "H1"
        cases.append(_case(room, plan, mut.give_room_to_another_hub(good, plan, room, other), {"COVERAGE_DUPLICATE"}, "claim_twice"))
    for room in ("R04", "R01", "R05"):  # rooms whose move leaves a hub with unconnected rooms
        cases.append(_case(f"{room}->H2", plan, mut.move_room(good, plan, room, "H2"), {"ZONE_DISCONNECTED"}, "split_zone"))
    return cases


CASES = _build_cases()


def codes_of(result):
    return {v.type for v in result.violations}


# ------------------------------------------------------------- baselines


@pytest.mark.parametrize(("name", "plan", "good"), BOTH, ids=[b[0] for b in BOTH])
def test_the_starting_plans_are_valid(name, plan, good):
    assert check_design(plan, good).valid


def test_there_are_plenty_of_damage_cases():
    assert len(CASES) > 150


# ----------------------------------------------- damage must be noticed


@pytest.mark.parametrize(("plan", "design", "expected", "family"), CASES)
def test_every_damaged_plan_is_caught_for_the_right_reason(plan, design, expected, family):
    result = check_design(plan, design)
    found = codes_of(result)
    assert not result.valid, f"{family} slipped through"
    assert expected <= found, f"{family}: wanted {expected}, got {found}"
    assert result.metrics is not None
    assert found <= set(VIOLATION_CODES)
    assert any(not check.passed for check in result.checks)


def test_the_damage_cases_cover_every_rule_the_checker_has():
    reached: set[str] = set()
    for param in CASES:
        plan, design, _, _ = param.values
        reached |= codes_of(check_design(plan, design))
    assert reached == set(VIOLATION_CODES), f"never triggered: {set(VIOLATION_CODES) - reached}"


@pytest.mark.parametrize(
    ("family", "exactly"),
    [
        ("drop_node", {"RING_NODE_MISSING"}),
        ("visit_twice", {"RING_NODE_REPEATED"}),
        # the cable then ends at the hub's spot, not at the node the ring claims to end at
        ("end_at_node", {"RING_NOT_CLOSED", "NODE_MOVED"}),
        ("end_other_hub", {"RING_WRONG_HUB"}),
        ("slant", {"SEGMENT_NOT_AXIS_ALIGNED"}),
    ],
)
def test_clean_damage_trips_only_its_own_rule(family, exactly):
    chosen = [p for p in CASES if p.values[3] == family]
    assert chosen
    for param in chosen:
        plan, design, _, _ = param.values
        assert codes_of(check_design(plan, design)) == exactly, param.id


# ------------------------------------------- several damages at once


def test_two_independent_damages_are_both_reported():
    _, plan, good = SLIDE5
    damaged = mut.slant_leg(good, "H1", 0)
    damaged = mut.drop_node_from_ring(damaged, "H2", "N2")
    damaged = mut.shift_node(damaged, "N6")
    found = codes_of(check_design(plan, damaged))
    assert {"SEGMENT_NOT_AXIS_ALIGNED", "RING_NODE_MISSING", "NODE_MOVED"} <= found


def test_damage_in_every_hub_is_reported_for_every_hub():
    _, plan, good = SLIDE5
    damaged = mut.start_ring_away_from_hub(mut.start_ring_away_from_hub(good, "H1"), "H2")
    result = check_design(plan, damaged)
    assert {v.hub_id for v in result.violations if v.type == "RING_NOT_CLOSED"} == {"H1", "H2"}


def test_violations_point_at_the_hub_that_was_damaged():
    _, plan, good = SLIDE5
    for hub_id in ("H1", "H2"):
        result = check_design(plan, mut.drop_node_from_ring(good, hub_id, good.hubs[0 if hub_id == "H1" else 1].assigned_nodes[0]))
        assert {v.hub_id for v in result.violations} == {hub_id}


# ------------------------------------- harmless changes must stay valid


@pytest.mark.parametrize(("name", "plan", "good"), BOTH, ids=[b[0] for b in BOTH])
def test_splitting_straight_pieces_changes_nothing(name, plan, good):
    before, after = check_design(plan, good), check_design(plan, mut.split_straight_pieces(good))
    assert after.valid
    assert (after.metrics.L, after.metrics.X, after.metrics.O, after.metrics.B) == pytest.approx(
        (before.metrics.L, before.metrics.X, before.metrics.O, before.metrics.B)
    )
    assert after.metrics.J == pytest.approx(before.metrics.J)


@pytest.mark.parametrize(("name", "plan", "good"), BOTH, ids=[b[0] for b in BOTH])
def test_nudging_everything_by_less_than_a_millimetre_stays_valid(name, plan, good):
    result = check_design(plan, mut.nudge_everything(good))
    assert result.valid
    assert result.metrics.J == pytest.approx(check_design(plan, good).metrics.J, abs=1e-6)


@pytest.mark.parametrize(("name", "plan", "good"), BOTH, ids=[b[0] for b in BOTH])
def test_visiting_the_nodes_in_the_opposite_order_stays_valid_and_costs_the_same(name, plan, good):
    before, after = check_design(plan, good), check_design(plan, mut.reverse_every_ring(good))
    assert after.valid
    assert after.metrics.J == pytest.approx(before.metrics.J)
    assert (after.metrics.X, after.metrics.B) == (before.metrics.X, before.metrics.B)
    assert after.metrics.O == pytest.approx(before.metrics.O)


@pytest.mark.parametrize("hub_id", ["H1", "H2"])
def test_reversing_just_one_ring_stays_valid(hub_id):
    _, plan, good = SLIDE5
    reversed_one = mut.reverse_ring(good, hub_id)
    assert reversed_one.hubs[0 if hub_id == "H1" else 1].ring.sequence[1] != good.hubs[0 if hub_id == "H1" else 1].ring.sequence[1]
    assert check_design(plan, reversed_one).valid


def test_reversing_twice_gives_the_same_design_back():
    _, _, good = SLIDE5
    assert mut.reverse_every_ring(mut.reverse_every_ring(good)) == good


@pytest.mark.parametrize("build", [designs.slide5_with_crossing, designs.slide5_with_overlap])
def test_crossings_and_overlaps_only_cost_points(build):
    _, plan, good = SLIDE5
    base, costly = check_design(plan, good), check_design(plan, build())
    assert costly.valid and costly.violations == []
    assert is_better(base.metrics, costly.metrics)


def test_a_cable_only_grazing_the_edge_of_a_door_is_not_a_wall_hit():
    # H2 sits at y = 6.0, exactly the bottom edge of the bathroom door's gap in the x = 12 wall.
    _, plan, good = SLIDE5
    grazing = mut.spike(good, "H2", "H2", 0, "E", 1.3)
    found = codes_of(check_design(plan, grazing))
    assert "WALL_INTERSECTION" not in found
    assert "OUTSIDE_INNER" not in found


def test_adding_crossing_and_overlap_on_top_of_a_split_plan_stays_valid():
    _, plan, _ = SLIDE5
    both = mut.split_straight_pieces(designs.slide5_with_crossing())
    result = check_design(plan, both)
    assert result.valid and result.metrics.X == 1


def test_mutations_never_change_the_original_design():
    _, _, good = SLIDE5
    snapshot = good.model_dump()
    mut.drop_node_from_ring(good, "H1", "N5")
    mut.spike(good, "H1", "H1", 0, "E", 1.0)
    mut.reverse_every_ring(good)
    assert good.model_dump() == snapshot
