import networkx as nx
import math
import time
from shapely.geometry import LineString, MultiPolygon, Polygon

from othello.models import SolverConfig
from othello.reader import build_floor_plan_from_resplan
from othello.run import run_plan
from othello.io.official import design_from_official
from othello.checker import check_design
from othello.territory import generate_partitions, hub_lower_bound


def rectangle(x1, y1, x2, y2):
    return Polygon([(x1, y1), (x2, y1), (x2, y2), (x1, y2)])


def sample_record():
    living = rectangle(0, 0, 50, 50)
    bedroom = rectangle(60, 0, 100, 50)
    kitchen = rectangle(0, 60, 50, 100)
    return {
        "id": 1,
        "area": 100.0,
        "inner": rectangle(0, 0, 100, 100),
        "wall": MultiPolygon([]),
        "window": MultiPolygon([]),
        "door": MultiPolygon([]),
        "living": living,
        "bedroom": MultiPolygon([bedroom]),
        "kitchen": kitchen,
        "graph": nx.Graph(),
    }


def door_connected_record():
    return {
        "id": "door-connected",
        "area": 100.0,
        "inner": rectangle(0, 0, 100, 100),
        "wall": MultiPolygon([
            rectangle(48, 0, 52, 100),
            rectangle(50, 48, 100, 52),
        ]),
        "window": MultiPolygon([]),
        "door": MultiPolygon([
            rectangle(48, 20, 52, 30),
            rectangle(48, 70, 52, 80),
        ]),
        "living": rectangle(0, 0, 50, 100),
        "bedroom": MultiPolygon([rectangle(50, 0, 100, 50)]),
        "kitchen": rectangle(50, 50, 100, 100),
        "graph": nx.Graph(),
    }


def test_normalises_scale_and_generates_development_nodes():
    plan = build_floor_plan_from_resplan(sample_record())
    assert plan.scale == 10.0
    assert len(plan.nodes) == 3
    assert plan.total_eligible_area_m2() == 65.0
    assert all(room.area_m2 > 0 for room in plan.eligible_rooms())


def test_clipped_eligible_room_remains_an_interior_room():
    record = sample_record()
    record["living"] = rectangle(-0.00001, 0, 50, 50)
    plan = build_floor_plan_from_resplan(record)
    room = next(room for room in plan.rooms.values() if room.kind == "living")
    assert room.polygon_m.difference(plan.inner_m).area == 0
    assert room.inside_inner


def test_lower_bound_and_disconnected_partitions():
    plan = build_floor_plan_from_resplan(sample_record())
    assert hub_lower_bound(plan) == 1
    partitions, status = generate_partitions(plan, 1, SolverConfig())
    assert partitions == []
    assert status == "proven_infeasible"


def test_door_links_form_a_connected_territory():
    plan = build_floor_plan_from_resplan(door_connected_record())
    partitions, status = generate_partitions(plan, 1)
    assert status == "feasible"
    assert len(partitions) == 1
    assert partitions[0].territories[0].room_ids == ["R01", "R02", "R03"]


def test_small_reader_record_reaches_independently_valid_official_output(tmp_path):
    """Exercise reader, partitions, solver, runner, and checker together in CI."""
    import json

    config = SolverConfig(time_budget_s=20)
    plan = build_floor_plan_from_resplan(door_connected_record(), config)
    outcome = run_plan(plan, tmp_path, config, picture=False)
    assert outcome.status == "ok", (outcome.failure, outcome.notes)
    official = json.loads(outcome.files["official"].read_text(encoding="utf-8"))
    assert check_design(plan, design_from_official(plan, official), config).valid


def test_rooms_are_repaired_and_made_disjoint():
    record = door_connected_record()
    record["living"] = Polygon([(0, 0), (50, 100), (0, 100), (50, 0)])
    plan = build_floor_plan_from_resplan(record)
    assert plan.walls_m.is_valid
    assert all(room.polygon_m.is_valid for room in plan.rooms.values())
    assert all(
        first.polygon_m.intersection(second.polygon_m).area <= 1e-12
        for index, first in enumerate(plan.rooms.values())
        for second in list(plan.rooms.values())[index + 1:]
    )


def test_overlapping_wall_parts_are_merged_before_repair():
    plan = build_floor_plan_from_resplan(door_connected_record())
    # The source wall parts overlap by 8 square units.  They must form one
    # solid 592-unit wall, not a 584-unit invalid-geometry repair with a hole.
    # The two 40-unit door voids are subsequently cut from that wall.
    assert math.isclose(plan.walls_m.area, 5.12, rel_tol=1e-12)


def test_door_gaps_cross_the_doorway_in_free_space_between_two_rooms():
    plan = build_floor_plan_from_resplan(door_connected_record())
    gaps = []
    for opening in plan.openings.values():
        if opening.kind != "door":
            continue
        gap = LineString(opening.gap_polyline_m)
        gaps.append(gap)
        assert len(opening.rooms) == 2
        assert gap.length > 0
        assert plan.free_space_m.covers(gap)
        assert gap.intersection(plan.walls_m).length == 0
        assert gap.intersection(plan.windows_m).length == 0
    # In this fixture, walls lie vertically and travel is horizontal.  Door
    # gaps must be vertical (across the wall), so routing edges cross them.
    assert gaps
    assert all(abs(gap.coords[0][0] - gap.coords[-1][0]) <= 1e-12 for gap in gaps)


def test_adjacency_is_not_a_zone_link():
    record = sample_record()
    record["graph"].add_edge("living_0", "bedroom_0", type="adjacency")
    record["graph"].add_edge("living_0", "kitchen_0", type="adjacency")
    plan = build_floor_plan_from_resplan(record)
    assert all(not neighbours for neighbours in plan.room_graph.values())


def test_development_node_survives_an_empty_hub_zone():
    record = sample_record()
    record["living"] = rectangle(0, 0, 4, 4)
    record["inner"] = rectangle(0, 0, 100, 100)
    record["wall"] = rectangle(0, 0, 4, 4)
    plan = build_floor_plan_from_resplan(record)
    assert len(plan.nodes) == 3
    assert plan.hub_zones_m["R01"].is_empty


def test_hub_zone_and_development_node_exclude_window_clearance():
    record = sample_record()
    record["window"] = rectangle(20, 20, 30, 30)
    plan = build_floor_plan_from_resplan(record)
    window_m = plan.windows_m.buffer(0.5)
    assert plan.hub_zones_m["R01"].intersection(window_m).area <= 1e-12
    assert plan.free_space_m.covers(plan.hub_zones_m["R01"])


def test_every_hub_zone_point_has_required_wall_and_window_clearance():
    record = sample_record()
    record["wall"] = rectangle(0, 0, 4, 4)
    record["window"] = rectangle(20, 20, 30, 30)
    config = SolverConfig()
    plan = build_floor_plan_from_resplan(record, config)
    for zone in plan.hub_zones_m.values():
        if zone.is_empty:
            continue
        assert zone.distance(plan.walls_m) >= config.hub_wall_clearance_m
        assert zone.distance(plan.windows_m) >= config.hub_wall_clearance_m


def test_hub_zone_is_clipped_to_the_inner_free_space():
    record = sample_record()
    record["living"] = rectangle(-10, 0, 50, 50)
    plan = build_floor_plan_from_resplan(record)
    assert plan.free_space_m.covers(plan.hub_zones_m["R01"])


def test_geometry_verified_open_passage_is_a_zone_link():
    record = sample_record()
    record["wall_depth"] = 2.0
    record["living"] = rectangle(0, 0, 50, 50)
    record["bedroom"] = MultiPolygon([rectangle(50, 0, 100, 50)])
    plan = build_floor_plan_from_resplan(record)
    assert "R02" in plan.room_graph["R01"]
    assert [opening.kind for opening in plan.openings.values()] == ["open_passage"]


def test_resplan_8554_passage_uses_open_span_instead_of_corner_edge():
    """The source passage has a long shared opening but a tiny first polygon edge."""
    import json
    from pathlib import Path

    from shapely import wkt

    from othello.reader.load_plan import _passage_gap_polyline

    fixture = json.loads((Path(__file__).parent / "data" / "resplan_8554_passage.json")
                         .read_text(encoding="utf-8"))
    assert fixture["source_plan_id"] == "8554"
    room_a = wkt.loads(fixture["room_a_wkt"])
    room_b = wkt.loads(fixture["room_b_wkt"])
    gap = wkt.loads(fixture["candidate_gap_wkt"])
    line = _passage_gap_polyline(room_a, room_b, gap, SolverConfig().tolerance_m)
    assert line is not None
    assert LineString(line).length > 3.8
    assert abs(line[0][1] - line[1][1]) < 1e-9
    assert gap.covers(LineString(line))


def test_scale_matches_the_problem_statement_example():
    record = sample_record()
    record["inner"] = rectangle(0, 0, 200, 180)
    record["area"] = 110.0
    plan = build_floor_plan_from_resplan(record)
    assert math.isclose(plan.scale, math.sqrt(36_000 / 110), rel_tol=1e-12)


def test_expired_partition_deadline_is_unresolved():
    record = sample_record()
    record["graph"].add_edge("living_0", "bedroom_0", type="via_door")
    record["graph"].add_edge("living_0", "kitchen_0", type="via_door")
    plan = build_floor_plan_from_resplan(record)
    partitions, status = generate_partitions(plan, 1, deadline=time.monotonic() - 1)
    assert partitions == []
    assert status == "unresolved"
