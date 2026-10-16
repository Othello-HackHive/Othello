"""Checker-led local improvements without shared Part 1 or Part 2 models."""

from copy import deepcopy
from pathlib import Path
import sys
from types import SimpleNamespace as NS

import pytest
from shapely.geometry import LineString

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.routing import RoutingCache
from othello.routing.types import RouteLibraryResult, RouteVariant
from othello.solver import construct_f0_design, improve_design
from othello.solver import improvement, route_selection
from othello.solver.types import HubCandidateResult, NodeOrderResult, RouteSelectionResult
from othello.solver.tests.test_f0 import (
    completed_plan, config as f0_config, excluded_detour_plan, single_door_plan,
)
from othello.solver.tests._doubles import make_opening


def _config(*, hubs=12, orders=12):
    return f0_config(
        time_budget_s=10.0,
        weights=NS(length=1.0, bend=0.5, crossing=5.0, overlap=1.0),
        limits=NS(hub_candidates=hubs, orders_kept=orders, routes_per_leg=1,
                  beam_width=4, exact_combo_limit=30),
    )


def _geometry_checked(plan, design):
    for hub in design["hubs"]:
        sequence = hub["ring"]["sequence"]
        assert sequence[0] == sequence[-1] == hub["hub_id"]
        assert sorted(sequence[1:-1]) == sorted(hub["assigned_nodes"])
        stops = {hub["hub_id"]: tuple(hub["location"])}
        stops.update({node: tuple(plan.nodes[node].point_m) for node in hub["assigned_nodes"]})
        for (source, target), leg in zip(zip(sequence, sequence[1:]), hub["ring"]["legs"]):
            assert (leg["from_id"], leg["to_id"]) == (source, target)
            assert tuple(leg["polyline"][0]) == stops[source]
            assert tuple(leg["polyline"][-1]) == stops[target]
            for a, b in zip(leg["polyline"], leg["polyline"][1:]):
                if a == b:
                    assert stops[source] == stops[target]
                    continue
                assert (a[0] == b[0]) != (a[1] == b[1])
                segment = LineString([a, b])
                assert plan.free_space_m.covers(segment)
                assert plan.inner_m.covers(segment)
                assert segment.disjoint(plan.walls_m)


def _valid(score, calls=None):
    def checker(plan, design, config):
        _geometry_checked(plan, design)
        if calls is not None:
            calls.append(deepcopy(design))
        return NS(valid=True, metrics=NS(J=float(score(design))))
    return checker


def _one_node_design(plan):
    hub, node = (3.0, 3.0), (1.0, 1.0)
    return {"plan_id": plan.plan_id, "solver_notes": {}, "hubs": [{
        "hub_id": "H1", "room_id": "R1", "location": hub,
        "assigned_rooms": ["R1"], "assigned_nodes": ["N1"],
        "ring": {"hub_id": "H1", "sequence": ["H1", "N1", "H1"],
                 "legs": [{"from_id": "H1", "to_id": "N1",
                           "polyline": [hub, (1.0, 3.0), node], "doors_used": []},
                          {"from_id": "N1", "to_id": "H1",
                           "polyline": [node, (1.0, 3.0), hub], "doors_used": []}]},
    }]}


def test_hub_move_discovers_better_complete_design_and_is_deterministic():
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    calls = []
    checker = _valid(lambda d: sum(LineString(leg["polyline"]).length
                                   for hub in d["hubs"] for leg in hub["ring"]["legs"]), calls)
    config = _config(hubs=6)
    warm = RoutingCache()
    first = improve_design(plan, design, config, checker, warm)
    first_calls = len(calls)
    again = improve_design(plan, design, config, checker, warm)
    fresh = improve_design(plan, design, config, checker, RoutingCache())
    assert first == again == fresh
    assert first.status == "improved" and first.verified and first.J == 0.0
    assert first.checked_designs == first_calls
    assert len({improvement._design_key(candidate) for candidate in calls[:first_calls]}) == first_calls
    assert tuple(first.design["hubs"][0]["location"]) == (1.0, 1.0)
    assert design["hubs"][0]["location"] == (3.0, 3.0)


def test_checker_never_replaces_incumbent_with_worse_hub_move():
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    checker = _valid(lambda d: 0.0 if tuple(d["hubs"][0]["location"]) == (3.0, 3.0) else 20.0)
    result = improve_design(plan, design, _config(hubs=6), checker)
    assert result.status == "unchanged" and result.J == 0.0
    assert result.design == design and result.moves_considered > 0


def test_checker_failures_during_moves_keep_verified_incumbent_and_allow_recovery():
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)

    def checker(plan, candidate, config):
        _geometry_checked(plan, candidate)
        point = tuple(candidate["hubs"][0]["location"])
        if point == (3.0, 3.0):
            return NS(valid=True, metrics=NS(J=5.0))
        if point == (1.0, 1.0):
            return NS(valid=True, metrics=NS(J=1.0))
        raise RuntimeError("checker unavailable for candidate")

    result = improve_design(plan, design, _config(hubs=6), checker)
    assert result.status == "improved" and result.verified and result.J == 1.0
    assert tuple(result.design["hubs"][0]["location"]) == (1.0, 1.0)
    assert any("checker unavailable" in detail for detail in result.diagnostics)


def test_malformed_checker_baseline_cannot_verify_but_later_move_can():
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)

    def checker(plan, candidate, config):
        if tuple(candidate["hubs"][0]["location"]) == (3.0, 3.0):
            return NS(valid=True, metrics=None)
        return NS(valid=True, metrics=NS(J=2.0))

    result = improve_design(plan, design, _config(hubs=6), checker)
    assert result.status == "verified" and result.verified and result.J == 2.0
    assert tuple(result.design["hubs"][0]["location"]) != (3.0, 3.0)
    assert any("checker failed" in detail for detail in result.diagnostics)


def test_candidate_limit_error_during_improvement_preserves_verified_design(monkeypatch):
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    plan.openings["LEFT"] = make_opening("LEFT", [(0, 1.5), (0, 2.5)])
    plan.openings["RIGHT"] = make_opening("RIGHT", [(4, 1.5), (4, 2.5)])
    design = _one_node_design(plan)
    monkeypatch.setattr(improvement, "select_whole_design_routes",
                        lambda *args: RouteSelectionResult("no_verified_design"))
    monkeypatch.setattr(improvement, "enumerate_visiting_orders",
                        lambda *args: NodeOrderResult("unreachable_on_grid"))
    result = improve_design(plan, design, _config(hubs=1), _valid(lambda _: 4.0))
    assert result.status == "candidate_configuration_error"
    assert result.verified and result.J == 4.0 and result.design == design
    assert any("hub_candidates=1" in detail for detail in result.diagnostics)


def test_node_order_move_preserves_all_visits_and_finds_better_order():
    plan, option = single_door_plan()
    config = _config(hubs=4, orders=2)
    baseline = construct_f0_design(plan, option, config)
    assert baseline.status == "constructed_unverified"
    original = tuple(baseline.design["hubs"][0]["ring"]["sequence"])
    alternative = ("H1", *reversed(original[1:-1]), "H1")
    checker = _valid(lambda d: 1.0 if tuple(d["hubs"][0]["ring"]["sequence"]) == alternative else 10.0)
    result = improve_design(plan, baseline.design, config, checker)
    assert result.status == "improved" and result.J == 1.0
    assert tuple(result.design["hubs"][0]["ring"]["sequence"]) == alternative
    assert sum(leg["doors_used"].count("D1")
               for leg in result.design["hubs"][0]["ring"]["legs"]) == 2


def test_route_substitution_is_checked_and_can_improve(monkeypatch):
    plan, _ = completed_plan(node_point=(3.0, 1.0))
    hub, node = (1.0, 1.0), (3.0, 1.0)
    design = {"plan_id": plan.plan_id, "solver_notes": {}, "hubs": [{
        "hub_id": "H1", "room_id": "R1", "location": hub,
        "assigned_rooms": ["R1"], "assigned_nodes": ["N1"],
        "ring": {"hub_id": "H1", "sequence": ["H1", "N1", "H1"],
                 "legs": [{"from_id": "H1", "to_id": "N1", "polyline": [hub, node], "doors_used": []},
                          {"from_id": "N1", "to_id": "H1", "polyline": [node, hub], "doors_used": []}]},
    }]}
    monkeypatch.setattr(improvement, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("no_candidates"))

    def variant(points):
        polyline = tuple(points)
        return RouteVariant(polyline, LineString(points).length, LineString(points).length,
                            0, 0, (), None, None, ("test",))

    def library(table, source, target, config, deadline, **kwargs):
        if (source, target) == ("H1", "N1"):
            return RouteLibraryResult("ready", (variant([hub, node]),
                                                variant([hub, (1.0, 2.0), (3.0, 2.0), node])))
        return RouteLibraryResult("ready", (variant([node, hub]),))

    monkeypatch.setattr(route_selection, "build_route_library", library)
    checker = _valid(lambda d: 1.0 if len(d["hubs"][0]["ring"]["legs"][0]["polyline"]) > 2 else 10.0)
    result = improve_design(plan, design, _config(), checker)
    assert result.status == "improved" and result.J == 1.0
    assert result.design["hubs"][0]["ring"]["legs"][0]["polyline"][1] == (1.0, 2.0)


def test_timeout_and_unsupported_geometry_preserve_verified_incumbent(monkeypatch):
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    checker = _valid(lambda d: 4.0)
    monkeypatch.setattr(improvement, "select_whole_design_routes",
                        lambda *args: RouteSelectionResult("no_verified_design"))
    monkeypatch.setattr(improvement, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("timeout", diagnostics=("expired",)))
    timed = improve_design(plan, design, _config(), checker)
    assert timed.status == "timeout" and timed.verified and timed.J == 4.0
    assert timed.design == design
    monkeypatch.setattr(improvement, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("unsupported_geometry", diagnostics=("bad gap",)))
    unsupported = improve_design(plan, design, _config(), checker)
    assert unsupported.status == "unsupported_geometry" and unsupported.verified
    assert unsupported.design == design and "bad gap" in unsupported.diagnostics


def test_timeout_after_a_better_route_keeps_that_verified_route(monkeypatch):
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    better = deepcopy(design)
    better["hubs"][0]["ring"]["legs"][0]["polyline"] = [(3.0, 3.0), (3.0, 1.0), (1.0, 1.0)]

    def selector(plan, design, config, checker, cache, deadline):
        checked = checker(plan, better, config)
        return RouteSelectionResult("verified", better, True, checked.metrics.J)

    monkeypatch.setattr(improvement, "select_whole_design_routes", selector)
    monkeypatch.setattr(improvement, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("timeout"))
    checker = _valid(lambda d: 1.0 if d["hubs"][0]["ring"]["legs"][0]["polyline"][1] == (3.0, 1.0) else 5.0)
    result = improve_design(plan, design, _config(), checker)
    assert result.status == "timeout" and result.verified and result.J == 1.0
    assert result.design == better


def test_malformed_route_selector_output_cannot_change_coverage(monkeypatch):
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    malformed = deepcopy(design)
    malformed["hubs"][0]["assigned_nodes"] = []
    monkeypatch.setattr(improvement, "select_whole_design_routes",
                        lambda *args: RouteSelectionResult("verified", malformed, True, 0.0))
    with pytest.raises(ValueError, match="broken ring"):
        improve_design(plan, design, _config(), _valid(lambda d: 5.0))


@pytest.mark.parametrize("policy", ["forbid", "avoid_if_possible"])
def test_local_routes_keep_excluded_room_policy(monkeypatch, policy):
    plan, option = excluded_detour_plan()
    config = _config(orders=3)
    config.cable_transit_policy = policy
    baseline = construct_f0_design(plan, option, config)
    assert baseline.status == "constructed_unverified"
    monkeypatch.setattr(improvement, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("no_candidates"))
    excluded = plan.rooms["X"].polygon_m

    def checker(plan, design, config):
        _geometry_checked(plan, design)
        for hub in design["hubs"]:
            for leg in hub["ring"]["legs"]:
                for a, b in zip(leg["polyline"], leg["polyline"][1:]):
                    if a != b:
                        assert LineString([a, b]).intersection(excluded).length == 0
        return NS(valid=True, metrics=NS(J=1.0))

    result = improve_design(plan, baseline.design, config, checker)
    assert result.verified and result.status in {"unchanged", "improved"}


def test_missing_checker_and_all_invalid_results_are_explicit(monkeypatch):
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    no_checker = improve_design(plan, design, _config())
    assert no_checker.status == "unverified" and no_checker.design is None
    assert no_checker.provisional_design == design and no_checker.J is None

    monkeypatch.setattr(improvement, "generate_hub_candidates",
                        lambda *args: HubCandidateResult("no_candidates"))
    invalid = improve_design(plan, design, _config(),
                             lambda *args: NS(valid=False, metrics=None))
    assert invalid.status == "no_verified_design" and invalid.design is None
    assert invalid.provisional_design == design and invalid.J is None


def test_immediately_expired_deadline_returns_no_unchecked_design():
    plan, _ = completed_plan(node_point=(1.0, 1.0))
    design = _one_node_design(plan)
    result = improve_design(plan, design, _config(), _valid(lambda d: 1.0), deadline=0.0)
    assert result.status == "timeout" and result.design is None and result.J is None
    assert not result.verified and result.provisional_design == design
