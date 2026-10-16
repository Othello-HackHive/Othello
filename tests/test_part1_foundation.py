import networkx as nx
import math
import time
from shapely.geometry import MultiPolygon, Polygon

from othello.models import SolverConfig
from othello.reader import build_floor_plan_from_resplan
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


def test_normalises_scale_and_generates_development_nodes():
    plan = build_floor_plan_from_resplan(sample_record())
    assert plan.scale == 10.0
    assert len(plan.nodes) == 3
    assert plan.total_eligible_area_m2() == 65.0
    assert all(room.area_m2 > 0 for room in plan.eligible_rooms())


def test_lower_bound_and_disconnected_partitions():
    plan = build_floor_plan_from_resplan(sample_record())
    assert hub_lower_bound(plan) == 1
    partitions, status = generate_partitions(plan, 1, SolverConfig())
    assert partitions == []
    assert status == "proven_infeasible"


def test_door_links_form_a_connected_territory():
    record = sample_record()
    record["graph"].add_edge("living_0", "bedroom_0", type="via_door")
    record["graph"].add_edge("living_0", "kitchen_0", type="via_door")
    plan = build_floor_plan_from_resplan(record)
    partitions, status = generate_partitions(plan, 1)
    assert status == "feasible"
    assert len(partitions) == 1
    assert partitions[0].territories[0].room_ids == ["R01", "R02", "R03"]


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
