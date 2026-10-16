from itertools import permutations
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from shapely.geometry import GeometryCollection

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.routing import DistanceTable, RoutingCache
from othello.solver import enumerate_visiting_orders
from othello.solver.tests._doubles import make_case, make_config


def config(cap=12):
    return make_config(limits=SimpleNamespace(orders_kept=cap))


class FakeTable:
    def __init__(self, stops, lengths=None, openings=None, failures=None):
        self.stops = {stop: (float(i), 0.0) for i, stop in enumerate(stops)}
        self.lengths = lengths or {}
        self.openings = openings or {}
        self.failures = failures or {}
        self.calls = []

    def get_route(self, source, target):
        self.calls.append((source, target))
        status = self.failures.get((source, target), "success")
        return SimpleNamespace(status=status, length_m=self.lengths.get((source, target), 1.0),
                               opening_ids=self.openings.get((source, target), ()),
                               diagnostics=(f"{source}->{target}: {status}",))


@pytest.mark.parametrize("count", [1, 2, 3, 4, 5])
def test_all_directed_permutations_and_hub_closure(count):
    nodes = [f"N{i}" for i in range(count)]
    table = FakeTable(["H", *nodes])
    result = enumerate_visiting_orders("H", nodes, table, config(cap=120))
    assert result.status == "ready"
    assert result.enumerated_count == len(result.orders) == len(list(permutations(nodes)))
    assert {order.node_ids for order in result.orders} == set(permutations(nodes))
    for order in result.orders:
        assert order.sequence == ("H", *order.node_ids, "H")
        assert len(set(order.node_ids)) == count
        assert order.estimated_length_m == count + 1
    assert len(table.calls) <= (count + 1) * count


def test_return_leg_changes_order_ranking():
    lengths = {("H", "A"): 1, ("H", "B"): 1, ("A", "B"): 1,
               ("B", "A"): 1, ("B", "H"): 1, ("A", "H"): 10}
    result = enumerate_visiting_orders("H", ["B", "A"], FakeTable(["H", "A", "B"], lengths), config())
    assert result.status == "ready"
    assert result.orders[0].sequence == ("H", "A", "B", "H")
    assert [order.estimated_length_m for order in result.orders] == [3, 12]


def test_door_diversity_retains_directed_alternatives():
    nodes = ["A", "B", "C"]
    openings = {("H", node): (f"FIRST_{node}",) for node in nodes}
    openings.update({(node, "H"): (f"LAST_{node}",) for node in nodes})
    result = enumerate_visiting_orders("H", nodes, FakeTable(["H", *nodes], openings=openings), config(cap=3))
    assert result.status == "ready"
    assert len(result.orders) == 3
    assert len({order.first_door_id for order in result.orders}) == 3
    assert len({order.last_door_id for order in result.orders}) == 3
    assert result.orders[0].node_ids == ("A", "B", "C")
    assert result.orders[0].estimated_opening_ids == ("FIRST_A", "LAST_C")


def test_deterministic_under_shuffled_input_and_equal_lengths():
    nodes = ["N3", "N1", "N2"]
    a = enumerate_visiting_orders("H", nodes, FakeTable(["H", *nodes]), config(cap=4))
    b = enumerate_visiting_orders("H", list(reversed(nodes)), FakeTable(["H", *reversed(nodes)]), config(cap=4))
    assert a == b
    assert a.orders[0].node_ids == ("N1", "N2", "N3")


def test_unreachable_pair_only_discards_affected_orders():
    table = FakeTable(["H", "A", "B"], failures={("H", "A"): "unreachable_on_grid"})
    result = enumerate_visiting_orders("H", ["A", "B"], table, config())
    assert result.status == "ready"
    assert [order.node_ids for order in result.orders] == [("B", "A")]
    assert result.enumerated_count == 2


def test_no_complete_order_is_not_proven_infeasible():
    table = FakeTable(["H", "A", "B"], failures={("H", "A"): "unreachable", ("H", "B"): "unreachable"})
    result = enumerate_visiting_orders("H", ["A", "B"], table, config())
    assert result.status == "unreachable_on_grid"
    assert result.enumerated_count == 2
    assert not result.orders
    assert "not proven" in result.diagnostics[0]


@pytest.mark.parametrize("failure", ["unsupported_geometry", "timeout"])
def test_fatal_route_status_returns_no_partial_shortlist(failure):
    table = FakeTable(["H", "A", "B"], failures={("A", "B"): failure})
    result = enumerate_visiting_orders("H", ["A", "B"], table, config())
    assert result.status == failure
    assert not result.orders
    assert result.diagnostics


def test_controlled_clock_timeout_during_enumeration(monkeypatch):
    from othello.solver import node_orders
    calls = 0
    def clock():
        nonlocal calls
        calls += 1
        return 0.0 if calls < 20 else 2.0
    monkeypatch.setattr(node_orders.time, "monotonic", clock)
    result = enumerate_visiting_orders("H", ["A", "B", "C"], FakeTable(["H", "A", "B", "C"]),
                                       config(), deadline=1.0)
    assert result.status == "timeout"
    assert not result.orders


def test_invalid_node_ids_and_cap():
    table = FakeTable(["H", "A"])
    with pytest.raises(ValueError, match="exactly once"):
        enumerate_visiting_orders("H", ["A", "A"], table, config())
    with pytest.raises(ValueError, match="1 to 5"):
        enumerate_visiting_orders("H", [], table, config())
    with pytest.raises(ValueError, match="positive integer"):
        enumerate_visiting_orders("H", ["A"], table, config(cap=0))


def test_real_distance_table_supplies_cached_obstacle_aware_estimates():
    plan, _ = make_case()
    plan.plan_id = "order-integration"
    plan.windows_m = GeometryCollection()
    plan.excluded_interior_rooms = []
    stops = {"H": (0.5, 0.5), "A": (3.5, 0.5), "B": (3.5, 3.5)}
    route_config = SimpleNamespace(grid_step_m=0.5, cable_wall_margin_m=0.02,
                                   tolerance_m=0.001, cable_transit_policy="forbid",
                                   weights=SimpleNamespace(length=1.0, bend=0.5))
    cache = RoutingCache()
    table = DistanceTable(plan, stops, route_config, cache)
    result = enumerate_visiting_orders("H", ["B", "A"], table, config())
    assert result.status == "ready", result.diagnostics
    assert len(result.orders) == 2
    assert all(order.estimated_length_m == 12.0 for order in result.orders)
    assert cache.stats().path_count == 6
