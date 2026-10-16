"""T05: the practice houses and cable plans are what they claim to be."""

import math

import pytest
from fixtures import designs
from fixtures.plans import SLIDE5_HUB_AREAS, TINY_HUB_AREAS, slide5_plan, tiny_plan
from shapely.geometry import LineString, Point

from othello.checker.metrics import compute_metrics

TOL = 1e-6


@pytest.fixture(scope="module")
def tiny():
    return tiny_plan()


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


def connected(rooms: list[str], graph: dict[str, list[str]]) -> bool:
    seen, todo = {rooms[0]}, [rooms[0]]
    while todo:
        for nxt in graph[todo.pop()]:
            if nxt in rooms and nxt not in seen:
                seen.add(nxt)
                todo.append(nxt)
    return seen == set(rooms)


def all_lines(design):
    for hub in design.hubs:
        for index, leg in enumerate(hub.ring.legs):
            yield hub.hub_id, index, LineString(leg.polyline)


# --------------------------------------------------------------- the houses


def test_tiny_plan_basics(tiny):
    assert [r.area_m2 for r in tiny.rooms.values()] == [16.0, 16.0]
    assert tiny.room_graph == {"R01": ["R02"], "R02": ["R01"]}
    assert all(not zone.is_empty for zone in tiny.hub_zones_m.values())


def test_slide5_rooms_and_areas(plan):
    eligible = {r.room_id: r.area_m2 for r in plan.eligible_rooms()}
    assert eligible == {"R01": 16.0, "R02": 16.0, "R03": 16.0, "R04": 15.0, "R05": 40.0, "R06": 15.0}
    assert plan.total_eligible_area_m2() == pytest.approx(118.0)
    assert len(plan.nodes) == 6
    # slide 5: 118 m2 and 6 nodes need at least 2 hubs
    assert max(math.ceil(118 / 100), math.ceil(6 / 5)) == 2


def test_bathroom_is_excluded_and_has_no_node(plan):
    assert not plan.rooms["R07"].eligible
    assert plan.excluded_interior_rooms == ["R07"]
    assert all(node.room_id != "R07" for node in plan.nodes.values())
    assert "R07" not in plan.room_graph


def test_entrance_does_not_link_rooms(plan):
    assert plan.openings["E1"].kind == "entrance"
    assert not plan.openings["E1"].counts_for_zone
    assert all(sum(1 for o in plan.openings.values() if r in o.rooms and o.counts_for_zone) >= 1 for r in plan.room_graph)


def test_good_zones_are_connected_and_a_bad_one_is_not(plan):
    assert connected(["R05", "R01", "R04"], plan.room_graph)  # H1
    assert connected(["R03", "R02", "R06"], plan.room_graph)  # H2
    assert not connected(["R03", "R02", "R06", "R04"], plan.room_graph)  # R04 only touches R05


def test_each_node_is_inside_its_room_and_half_a_metre_from_walls(plan):
    for node in plan.nodes.values():
        point = Point(node.point_m)
        assert plan.rooms[node.room_id].polygon_m.contains(point)
        assert point.distance(plan.walls_m) >= 0.5 - TOL


def test_unit_conversion_matches_the_scale(plan):
    node = plan.nodes["N1"]
    assert node.point_units == pytest.approx((node.point_m[0] * plan.scale, node.point_m[1] * plan.scale))


def test_free_space_leaves_out_walls_and_windows(plan):
    assert plan.free_space_m.area < plan.inner_m.area
    assert plan.free_space_m.intersection(plan.windows_m).area == pytest.approx(0.0)
    assert not plan.windows_m.is_empty
    # the middle of a door gap is free, the middle of a plain wall is not
    assert plan.free_space_m.contains(Point(3.5, 5.0))
    assert not plan.free_space_m.contains(Point(1.5, 5.0))


# --------------------------------------------------- the good cable plans


def test_tiny_design_numbers(tiny):
    m = compute_metrics(designs.tiny_design(), hub_areas=TINY_HUB_AREAS)
    assert (m.hub_count, m.X) == (1, 0)
    assert m.L == pytest.approx(10.0)
    assert m.O == pytest.approx(0.0)
    assert m.B == 6
    assert m.J == pytest.approx(13.0)


def test_slide5_design_numbers():
    m = compute_metrics(designs.slide5_design(), hub_areas=SLIDE5_HUB_AREAS)
    assert (m.hub_count, m.X, m.B) == (2, 0, 28)
    assert m.L == pytest.approx(50.8)
    assert m.O == pytest.approx(0.0)
    assert m.J == pytest.approx(64.8)
    assert m.area_gap_m2 == pytest.approx(24.0)
    assert m.per_hub["H1"]["ring_length_m"] == pytest.approx(27.2)
    assert m.per_hub["H1"]["bends"] == 12
    assert m.per_hub["H2"]["ring_length_m"] == pytest.approx(23.6)
    assert m.per_hub["H2"]["bends"] == 16


@pytest.mark.parametrize(
    "build",
    [designs.slide5_design, designs.slide5_with_crossing, designs.slide5_with_overlap],
)
def test_slide5_good_plans_keep_every_cable_in_free_space(plan, build):
    free = plan.free_space_m.buffer(1e-6)
    for hub_id, index, line in all_lines(build()):
        assert free.contains(line), f"{hub_id} leg {index} leaves free space"


def test_tiny_design_keeps_every_cable_in_free_space(tiny):
    free = tiny.free_space_m.buffer(1e-6)
    for _, _, line in all_lines(designs.tiny_design()):
        assert free.contains(line)


@pytest.mark.parametrize("build", [designs.slide5_design, designs.slide5_with_crossing, designs.slide5_with_overlap])
def test_good_plans_are_axis_aligned_and_chain_correctly(plan, build):
    design = build()
    for hub in design.hubs:
        ring = hub.ring
        assert ring.sequence[0] == ring.sequence[-1] == hub.hub_id
        assert len(ring.legs) == len(ring.sequence) - 1
        for a, b in zip(ring.legs, ring.legs[1:]):
            assert a.polyline[-1] == b.polyline[0]
        assert ring.legs[0].polyline[0] == hub.location == ring.legs[-1].polyline[-1]
        for leg in ring.legs:
            for p, q in zip(leg.polyline, leg.polyline[1:]):
                assert p[0] == q[0] or p[1] == q[1]
        for leg in ring.legs:  # the cable meets the real node points
            for stop, end in ((leg.from_id, leg.polyline[0]), (leg.to_id, leg.polyline[-1])):
                if stop in plan.nodes:
                    assert end == plan.nodes[stop].point_m


def test_hubs_sit_inside_their_zone(plan):
    design = designs.slide5_design()
    for hub in design.hubs:
        assert plan.hub_zones_m[hub.room_id].contains(Point(hub.location))
        assert hub.room_id in hub.assigned_rooms


def test_slide5_good_plan_covers_every_room_and_node_exactly_once():
    design = designs.slide5_design()
    rooms = [r for hub in design.hubs for r in hub.assigned_rooms]
    nodes = [n for hub in design.hubs for n in hub.assigned_nodes]
    assert sorted(rooms) == ["R01", "R02", "R03", "R04", "R05", "R06"]
    assert sorted(nodes) == ["N1", "N2", "N3", "N4", "N5", "N6"]


def test_costly_but_valid_plans_cost_more():
    base = compute_metrics(designs.slide5_design())
    crossing = compute_metrics(designs.slide5_with_crossing())
    overlap = compute_metrics(designs.slide5_with_overlap())
    assert crossing.X == 1 and crossing.J > base.J
    assert overlap.O == pytest.approx(1.3 + 3.5) and overlap.J > base.J


# ------------------------------------------------ the deliberately broken


def test_every_broken_plan_is_listed_and_builds():
    assert len(designs.ALL_BROKEN) == 13
    for build in designs.ALL_BROKEN.values():
        assert build().hubs  # builds without error


def test_missing_node_ring_skips_n4():
    hub = designs.broken_missing_node().hubs[0]
    assert "N4" in hub.assigned_nodes and "N4" not in hub.ring.sequence


def test_other_hub_ring_ends_wrong():
    ring = designs.broken_ring_ends_at_other_hub().hubs[0].ring
    assert ring.sequence[0] == "H1" and ring.sequence[-1] == "H2"


def test_capacity_plan_overloads_one_hub(plan):
    hub = designs.broken_capacity_all_in_one_hub().hubs[0]
    assert len(hub.assigned_nodes) == 6
    assert sum(plan.rooms[r].area_m2 for r in hub.assigned_rooms) > 100


def test_hub_in_wall_really_is_on_a_wall(plan):
    assert Point(designs.broken_hub_in_wall().hubs[0].location).intersects(plan.walls_m)


def test_hub_too_close_is_inside_room_but_under_half_a_metre(plan):
    hub = designs.broken_hub_too_close_to_wall().hubs[0]
    point = Point(hub.location)
    assert plan.rooms[hub.room_id].polygon_m.contains(point)
    assert point.distance(plan.walls_m) < 0.5


def test_window_plan_really_hits_a_window(plan):
    lines = [line for _, _, line in all_lines(designs.broken_cable_through_window())]
    assert any(line.intersects(plan.windows_m) for line in lines)


def test_wall_plan_really_hits_a_wall_and_no_window(plan):
    lines = [line for _, _, line in all_lines(designs.broken_cable_through_wall())]
    assert any(line.intersects(plan.walls_m) for line in lines)
    assert not any(line.intersects(plan.windows_m) for line in lines)


def test_diagonal_plan_has_a_slanted_piece():
    leg = designs.broken_diagonal_segment().hubs[0].ring.legs[0]
    p, q = leg.polyline[0], leg.polyline[1]
    assert p[0] != q[0] and p[1] != q[1]


def test_outside_plan_leaves_the_house(plan):
    lines = [line for _, _, line in all_lines(designs.broken_cable_outside_inner())]
    assert any(not plan.inner_m.buffer(1e-6).contains(line) for line in lines)


def test_disconnected_territory_lists(plan):
    hub = designs.broken_territory_disconnected().hubs[1]
    assert "R04" in hub.assigned_rooms
    assert not connected(hub.assigned_rooms, plan.room_graph)


def test_coverage_plans_break_the_exactly_once_rule():
    missing = designs.broken_coverage_missing()
    rooms = [r for hub in missing.hubs for r in hub.assigned_rooms]
    assert "R06" not in rooms
    dup = designs.broken_coverage_duplicate()
    rooms = [r for hub in dup.hubs for r in hub.assigned_rooms]
    assert rooms.count("R06") == 2


def test_moved_node_plan_misses_the_real_node(plan):
    leg = designs.broken_node_moved().hubs[0].ring.legs[2]
    assert leg.polyline[-1] != plan.nodes["N1"].point_m
    assert leg.polyline[-1] == (2.3, 7.0)
