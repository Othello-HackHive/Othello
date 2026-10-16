from types import SimpleNamespace
import math
from pathlib import Path
import sys

import pytest
from shapely.geometry import GeometryCollection, Point, Polygon, box
from shapely.ops import unary_union

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.solver.tests._doubles import make_case, make_config, make_node, make_opening, make_room
from othello.solver import generate_hub_candidates


@pytest.fixture
def basic_case():
    return make_case()


def _cap(value):
    return SimpleNamespace(hub_candidates=value)


def test_sources_exact_coordinates_and_feasibility(basic_case):
    plan, territory = basic_case
    plan.nodes["N1"].point_m = (1.125, 1.375)
    plan.openings["D1"] = make_opening("D1", [(0, 1), (0, 2)])
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready", result.diagnostics
    tags = {tag for candidate in result.candidates for tag in candidate.source_tags}
    assert {"grid", "node_aligned", "node_exact", "near_door", "deepest"} <= tags
    assert any(candidate.point_m == (1.125, 1.375) for candidate in result.candidates)
    assert any("D1" in candidate.nearby_opening_ids for candidate in result.candidates)
    for candidate in result.candidates:
        point = Point(candidate.point_m)
        assert plan.rooms[candidate.room_id].polygon_m.contains(point)
        assert plan.hub_zones_m[candidate.room_id].covers(point)
        assert plan.free_space_m.covers(point)


def test_one_node_proxy_is_twice_manhattan_distance(basic_case):
    plan, territory = basic_case
    plan.nodes["N1"].point_m = (1.125, 1.375)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready"
    for candidate in result.candidates:
        x, y = candidate.point_m
        assert candidate.attachment_proxy_m == pytest.approx(2 * (abs(x - 1.125) + abs(y - 1.375)))


def test_minimum_hub_node_distance_avoids_zero_length_legs(basic_case):
    plan, territory = basic_case
    config = make_config()
    config.min_hub_node_distance_m = 0.002
    result = generate_hub_candidates(plan, territory, config)
    assert result.status == "ready"
    node = plan.nodes["N1"].point_m
    assert all(math.dist(candidate.point_m, node) >= 0.002 - 1e-12
               for candidate in result.candidates)
    config.min_hub_node_distance_m = 0
    allowed = generate_hub_candidates(plan, territory, config)
    assert any(candidate.point_m == node for candidate in allowed.candidates)


def test_hub_zone_too_small_for_node_separation_returns_no_candidates():
    zone = Point(1, 1).buffer(0.0005)
    plan, territory = make_case(zones={"R1": zone})
    config = make_config()
    config.min_hub_node_distance_m = 0.002
    result = generate_hub_candidates(plan, territory, config)
    assert result.status == "no_candidates"


def test_two_distinct_nodes_with_same_coordinate_count_separately(basic_case):
    plan, territory = basic_case
    plan.nodes["N1"].point_m = (1, 1)
    plan.nodes["N2"] = make_node("N2", "R1", (1, 1))
    territory.node_ids.append("N2")
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready"
    for candidate in result.candidates:
        x, y = candidate.point_m
        assert candidate.attachment_proxy_m == pytest.approx(2 * (abs(x - 1) + abs(y - 1)))


def test_proxy_uses_two_smallest_distinct_node_distances(basic_case):
    plan, territory = basic_case
    plan.nodes["N2"] = make_node("N2", "R1", (4, 1))
    plan.nodes["N3"] = make_node("N3", "R1", (1, 4))
    territory.node_ids.extend(["N2", "N3"])
    result = generate_hub_candidates(plan, territory, make_config())
    for candidate in result.candidates:
        x, y = candidate.point_m
        distances = sorted(abs(x - nx) + abs(y - ny) for nx, ny in [(1, 1), (4, 1), (1, 4)])
        assert candidate.attachment_proxy_m == pytest.approx(sum(distances[:2]))


def test_empty_room_zone_is_skipped_but_other_room_is_kept():
    rooms = [make_room("R1", box(0, 0, 4, 4)), make_room("R2", box(4, 0, 8, 4))]
    zones = {"R1": GeometryCollection(), "R2": box(4.5, 0.5, 7.5, 3.5)}
    nodes = [make_node("N1", "R1", (1, 1)), make_node("N2", "R2", (6, 2))]
    plan, territory = make_case(rooms=rooms, zones=zones, nodes=nodes)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready", result.diagnostics
    assert {candidate.room_id for candidate in result.candidates} == {"R2"}


def test_all_empty_zones_return_no_candidates(basic_case):
    plan, territory = basic_case
    plan.hub_zones_m["R1"] = GeometryCollection()
    territory.hub_zone_m = GeometryCollection()
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "no_candidates"
    assert not result.candidates


def test_deepest_samples_span_disconnected_zone_components(basic_case):
    plan, territory = basic_case
    zone = unary_union([box(0.5, 0.5, 1.5, 1.5), box(2.5, 2.5, 3.5, 3.5)])
    plan.hub_zones_m["R1"] = zone
    territory.hub_zone_m = zone
    result = generate_hub_candidates(plan, territory, make_config(limits=_cap(2)))
    assert result.status == "ready", result.diagnostics
    assert len(result.candidates) == 2
    assert all("deepest" in candidate.source_tags for candidate in result.candidates)
    assert any(candidate.point_m[0] < 2 for candidate in result.candidates)
    assert any(candidate.point_m[0] > 2 for candidate in result.candidates)


def test_equivalent_union_with_reversed_ring_order_is_accepted(basic_case):
    plan, territory = basic_case
    territory.hub_zone_m = Polygon(list(reversed(list(plan.hub_zones_m["R1"].exterior.coords))))
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready"


def test_inconsistent_union_is_unsupported_not_a_clip(basic_case):
    plan, territory = basic_case
    territory.hub_zone_m = box(0, 0, 2, 4)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "unsupported_geometry"
    assert "union" in result.diagnostics[0]
    assert not result.candidates


def test_zone_outside_assigned_room_is_unsupported(basic_case):
    plan, territory = basic_case
    plan.hub_zones_m["R1"] = box(-1, 0, 4, 4)
    territory.hub_zone_m = plan.hub_zones_m["R1"]
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "unsupported_geometry"
    assert "outside" in result.diagnostics[0]


def test_ineligible_room_is_unsupported(basic_case):
    plan, territory = basic_case
    plan.rooms["R1"].eligible = False
    assert generate_hub_candidates(plan, territory, make_config()).status == "unsupported_geometry"


def test_overlap_with_an_unassigned_room_is_unsupported(basic_case):
    plan, territory = basic_case
    plan.rooms["EXCLUDED"] = make_room("EXCLUDED", box(3, 0, 5, 4), eligible=False)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "unsupported_geometry"
    assert "ambiguous ownership" in result.diagnostics[0]


def test_malformed_door_gap_is_unsupported(basic_case):
    plan, territory = basic_case
    plan.openings["D1"] = make_opening("D1", [(0, 1), (0, 1)])
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "unsupported_geometry"
    assert "gap geometry" in result.diagnostics[0]


def test_door_with_invalid_zone_metadata_is_not_silently_ignored(basic_case):
    plan, territory = basic_case
    door = make_opening("D1", [(0, 1), (0, 2)])
    door.counts_for_zone = False
    plan.openings["D1"] = door
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "unsupported_geometry"
    assert "must count" in result.diagnostics[0]


def test_one_candidate_covers_two_doorway_groups_after_dedup(basic_case):
    plan, territory = basic_case
    plan.hub_zones_m["R1"] = box(0.5, 0.5, 3.5, 3.5)
    territory.hub_zone_m = plan.hub_zones_m["R1"]
    plan.openings["D1"] = make_opening("D1", [(0, 1.5), (0, 2.5)])
    plan.openings["D2"] = make_opening("D2", [(0, 1.5), (0, 2.5)])
    result = generate_hub_candidates(plan, territory, make_config(limits=_cap(1)))
    assert result.status == "ready", result.diagnostics
    assert len(result.candidates) == 1
    assert result.candidates[0].nearby_opening_ids == ("D1", "D2")


def test_cap_rejected_only_when_distinct_door_representatives_are_needed(basic_case):
    plan, territory = basic_case
    plan.hub_zones_m["R1"] = box(0.5, 0.5, 3.5, 3.5)
    territory.hub_zone_m = plan.hub_zones_m["R1"]
    plan.openings["LEFT"] = make_opening("LEFT", [(0, 1.5), (0, 2.5)])
    plan.openings["RIGHT"] = make_opening("RIGHT", [(4, 1.5), (4, 2.5)])
    with pytest.raises(ValueError, match="distinct required representatives"):
        generate_hub_candidates(plan, territory, make_config(limits=_cap(1)))
    result = generate_hub_candidates(plan, territory, make_config(limits=_cap(2)))
    assert result.status == "ready"
    assert len(result.candidates) == 2
    assert {opening for candidate in result.candidates for opening in candidate.nearby_opening_ids} == {"LEFT", "RIGHT"}


def test_entrance_does_not_create_doorway_group(basic_case):
    plan, territory = basic_case
    plan.openings["FRONT"] = make_opening("FRONT", [(0, 1), (0, 2)], kind="entrance")
    result = generate_hub_candidates(plan, territory, make_config(limits=_cap(1)))
    assert result.status == "ready"
    assert result.candidates[0].nearby_opening_ids == ()


@pytest.mark.parametrize("offset", [0.0, 1000000.0])
def test_wall_clearance_roundoff_and_real_violation(offset):
    room = make_room("R1", box(offset, 0, offset + 4, 4))
    wall = box(offset - 0.25, 0, offset, 4)
    zone = box(offset + 0.5 - 1e-6, 0.25, offset + 3.75, 3.75)
    nodes = [make_node("N1", "R1", (offset + 0.5 - 1e-6, 2))]
    plan, territory = make_case(rooms=[room], zones={"R1": zone}, nodes=nodes, walls=wall)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready", result.diagnostics
    assert all(Point(candidate.point_m).distance(wall) >= 0.5 - 1e-9 for candidate in result.candidates)
    assert not any(candidate.point_m == nodes[0].point_m for candidate in result.candidates)
    assert any(math.isclose(candidate.point_m[0], offset + 0.5, abs_tol=1e-10) for candidate in result.candidates)


@pytest.mark.parametrize("offset", [0.0, 1000000.0])
def test_exact_clearance_survives_binary_subtraction_roundoff(offset):
    room = make_room("R1", box(offset + 0.2, 0, offset + 4, 4))
    wall = box(offset, 0, offset + 0.2, 4)
    exact = (offset + 0.7, 2.0)
    zone = box(exact[0], 0.5, offset + 3.5, 3.5)
    plan, territory = make_case(rooms=[room], zones={"R1": zone},
                                nodes=[make_node("N1", "R1", exact)], walls=wall)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready", result.diagnostics
    assert any(candidate.point_m == exact for candidate in result.candidates)


def test_shuffled_model_order_gives_identical_candidates():
    rooms = [make_room("R1", box(0, 0, 4, 4)), make_room("R2", box(4, 0, 8, 4))]
    zones = {"R1": box(0.5, 0.5, 3.5, 3.5), "R2": box(4.5, 0.5, 7.5, 3.5)}
    nodes = [make_node("N1", "R1", (1, 1)), make_node("N2", "R2", (7, 3))]
    openings = [make_opening("D1", [(4, 1), (4, 2)], rooms=("R1", "R2")),
                make_opening("D2", [(0, 1), (0, 2)])]
    first = make_case(rooms=rooms, zones=zones, nodes=nodes, openings=openings)
    second = make_case(rooms=list(reversed(rooms)), zones=dict(reversed(list(zones.items()))),
                       nodes=list(reversed(nodes)), openings=list(reversed(openings)),
                       room_ids=["R2", "R1"], node_ids=["N2", "N1"])
    config = make_config(limits=_cap(12))
    a = generate_hub_candidates(*first, config)
    b = generate_hub_candidates(*second, config)
    assert a == b


def test_deadline_during_generation_returns_no_partial(monkeypatch, basic_case):
    from othello.solver import hub_candidates
    count = 0
    def ticking():
        nonlocal count
        count += 1
        return 0.0 if count < 30 else 2.0
    monkeypatch.setattr(hub_candidates.time, "monotonic", ticking)
    result = generate_hub_candidates(*basic_case, make_config(), deadline=1.0)
    assert result.status == "timeout"
    assert not result.candidates


def test_expired_deadline_and_invalid_config(basic_case):
    assert generate_hub_candidates(*basic_case, make_config(), deadline=0.0).status == "timeout"
    with pytest.raises(ValueError, match="hub_grid_step_m"):
        generate_hub_candidates(*basic_case, make_config(hub_grid_step_m=0))
    with pytest.raises(ValueError, match="hub_wall_clearance_m"):
        generate_hub_candidates(*basic_case, make_config(hub_wall_clearance_m=0.49))


def test_selected_points_span_room_instead_of_only_shortest_proxy(basic_case):
    plan, territory = basic_case
    result = generate_hub_candidates(plan, territory, make_config(limits=_cap(8)))
    assert result.status == "ready"
    assert len(result.candidates) == 8
    assert any("deepest" in candidate.source_tags for candidate in result.candidates)
    points = [candidate.point_m for candidate in result.candidates]
    assert max(x for x, _ in points) - min(x for x, _ in points) >= 2
    assert max(y for _, y in points) - min(y for _, y in points) >= 2


def test_resplan_6492_recovers_the_r01_d2_doorway_witness():
    """The real narrow D2 zone was missed by grid and nearest-point samples."""
    dataset_path = Path(__file__).resolve().parents[4] / "resplan" / "ResPlan.pkl"
    if not dataset_path.exists():
        pytest.skip("optional ResPlan dataset is unavailable")
    from othello.models import SolverConfig
    from othello.reader.load_plan import build_floor_plan_from_resplan, load_resplan_dataset
    from othello.territory import generate_partitions, hub_lower_bound

    config = SolverConfig()
    record = next(record for record in load_resplan_dataset(dataset_path) if record["id"] == 6492)
    plan = build_floor_plan_from_resplan(record, config)
    options, status = generate_partitions(plan, hub_lower_bound(plan, config), config)
    assert status == "feasible"
    territory = next(territory for territory in options[0].territories if "R01" in territory.room_ids)
    result = generate_hub_candidates(plan, territory, config)
    assert result.status == "ready", result.diagnostics
    witnesses = [candidate for candidate in result.candidates
                 if candidate.room_id == "R01" and "D2" in candidate.nearby_opening_ids]
    assert witnesses
    gap = plan.openings["D2"].gap_polyline_m
    from shapely.geometry import LineString
    threshold = plan.hub_zones_m["R01"].distance(LineString(gap)) + config.hub_grid_step_m
    for candidate in witnesses:
        point = Point(candidate.point_m)
        assert plan.rooms["R01"].polygon_m.contains(point)
        assert plan.hub_zones_m["R01"].covers(point)
        assert plan.free_space_m.covers(point)
        assert point.distance(plan.walls_m) >= config.hub_wall_clearance_m - 1e-12
        assert point.distance(LineString(gap)) <= threshold + 1e-12


def test_witness_recovery_when_every_regular_sample_misses_clearance_sliver():
    room = make_room("R1", box(0, 0, 4, 4))
    wall = box(-1, 0, 0, 4)
    zone = Polygon([(0.4, 1.1), (0.52, 1.1), (0.4, 1.24)])
    door = make_opening("D1", [(0, 1.1), (0, 1.2)])
    plan, territory = make_case(rooms=[room], zones={"R1": zone},
                                openings=[door], walls=wall)
    result = generate_hub_candidates(plan, territory, make_config())
    assert result.status == "ready", result.diagnostics
    assert any("D1" in candidate.nearby_opening_ids for candidate in result.candidates)
    for candidate in result.candidates:
        point = Point(candidate.point_m)
        assert zone.covers(point)
        assert room.polygon_m.contains(point)
        assert point.distance(wall) >= 0.5 - 1e-14
