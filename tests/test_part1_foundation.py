import networkx as nx
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
