"""Task 3.9 search ordering, proof states, and checker isolation."""

from pathlib import Path
import sys
import time
from types import SimpleNamespace as NS

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from othello.solver import optimize_partitions
from othello.solver import outer, route_selection
from othello.solver.types import F0Result, ImprovementResult, RouteSelectionResult
from othello.solver.tests.test_f0 import completed_plan, config as f0_config


def _config():
    return NS(limits=NS(partitions_per_H=12, beam_width=2), time_budget_s=10.0)


def _option(label, hub_count=1, gap=0.0):
    territories = [NS(territory_id=f"T{i}", room_ids=[f"R{i}"], node_ids=[f"N{i}"])
                   for i in range(1, hub_count + 1)]
    return NS(label=label, hub_count=hub_count, area_gap_m2=gap, territories=territories)


def _design(option):
    hubs = []
    for i in range(1, option.hub_count + 1):
        hub, node = f"H{i}", f"N{i}"
        a, b = (float(i), 0.0), (float(i) + 1, 0.0)
        hubs.append({"hub_id": hub, "room_id": f"R{i}", "location": a,
                     "assigned_rooms": [f"R{i}"], "assigned_nodes": [node],
                     "ring": {"hub_id": hub, "sequence": [hub, node, hub],
                              "legs": [{"from_id": hub, "to_id": node, "polyline": [a, b], "doors_used": []},
                                       {"from_id": node, "to_id": hub, "polyline": [b, a], "doors_used": []}]}})
    return {"plan_id": "toy", "hubs": hubs, "solver_notes": {}, "test_label": option.label}


def _mock_pipeline(monkeypatch, scores, statuses=None):
    calls = []
    statuses = statuses or {}

    def f0(plan, option, config, cache, deadline):
        calls.append(option.label)
        status = statuses.get(option.label, "constructed_unverified")
        return F0Result(status, _design(option) if status == "constructed_unverified" else None)

    def checker(plan, design, config):
        score = scores.get(design["test_label"])
        return NS(valid=score is not None, metrics=NS(J=score) if score is not None else None)

    def select(plan, design, config, checker, cache, deadline):
        score = scores.get(design["test_label"])
        if score is None:
            return RouteSelectionResult("no_verified_design")
        return RouteSelectionResult("verified", design, True, score)

    monkeypatch.setattr(outer, "construct_f0_design", f0)
    monkeypatch.setattr(outer, "select_whole_design_routes", select)
    monkeypatch.setattr(outer, "_configuration_designs", lambda *args: ())
    monkeypatch.setattr(outer, "_improve_incumbent", lambda plan, design, config, checker, cache, deadline:
                        ImprovementResult("unchanged", design, verified=True,
                                          J=scores.get(design["test_label"])))
    return checker, calls


def test_verified_hub_count_precedes_j_and_stops_larger_counts(monkeypatch):
    checker, calls = _mock_pipeline(monkeypatch, {"small": 100.0, "large": 1.0})
    batches = {1: ([_option("small")], "feasible"),
               2: ([_option("large", 2)], "feasible")}
    result = optimize_partitions(NS(), batches, _config(), checker, lower_bound_hubs=1)
    assert result.status == "verified" and result.J == 100.0
    assert result.design["test_label"] == "small" and calls == ["small"]
    assert result.hub_count_status.tried == {1: "feasible"}
    assert result.hub_count_status.proven_minimum


def test_same_hub_count_uses_verified_j_then_area_gap(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"low_gap": 20.0, "low_j": 10.0})
    batch = {1: ([_option("low_gap", gap=1.0), _option("low_j", gap=8.0)], "feasible")}
    result = optimize_partitions(NS(), batch, _config(), checker, lower_bound_hubs=1)
    assert result.design["test_label"] == "low_j" and result.J == 10.0

    checker, _ = _mock_pipeline(monkeypatch, {"low_gap": 10.0, "high_gap": 10.0})
    batch = {1: ([_option("high_gap", gap=8.0), _option("low_gap", gap=1.0)], "feasible")}
    result = optimize_partitions(NS(), batch, _config(), checker, lower_bound_hubs=1)
    assert result.design["test_label"] == "low_gap"


def test_unresolved_lower_count_does_not_claim_minimal_hubs(monkeypatch):
    checker, calls = _mock_pipeline(monkeypatch, {"two": 3.0})
    batches = {1: ([_option("one")], "feasible"),
               2: ([_option("two", 2)], "feasible")}
    result = optimize_partitions(NS(), batches, _config(), checker, lower_bound_hubs=1)
    assert calls == ["one", "two"] and result.design["test_label"] == "two"
    assert result.hub_count_status.tried == {1: "unresolved", 2: "feasible"}
    assert not result.hub_count_status.proven_minimum

    batches[1] = ([], "proven_infeasible")
    result = optimize_partitions(NS(), batches, _config(), checker, lower_bound_hubs=1)
    assert result.hub_count_status.proven_minimum


def test_timeout_keeps_checker_verified_incumbent(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"first": 8.0}, {"second": "timeout"})
    batch = {1: ([_option("first", gap=0.0), _option("second", gap=1.0)], "feasible")}
    result = optimize_partitions(NS(), batch, _config(), checker, lower_bound_hubs=1)
    assert result.status == "timeout" and result.verified
    assert result.design["test_label"] == "first" and result.J == 8.0
    assert result.hub_count_status.tried[1] == "feasible"


def test_outer_accepts_only_better_verified_local_improvement(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"base": 10.0, "better": 3.0})
    option = _option("base")
    better = _design(option)
    better["test_label"] = "better"

    def improve(plan, design, config, injected_checker, cache, deadline):
        checked = injected_checker(plan, better, config)
        assert checked.valid
        return ImprovementResult("improved", better, verified=True, J=checked.metrics.J)

    monkeypatch.setattr(outer, "_improve_incumbent", improve)
    result = optimize_partitions(NS(), {1: ([option], "feasible")}, _config(), checker)
    assert result.status == "verified" and result.J == 3.0
    assert result.design["test_label"] == "better"

    monkeypatch.setattr(outer, "_improve_incumbent", lambda *args:
                        ImprovementResult("unchanged", better, verified=True, J=20.0))
    result = optimize_partitions(NS(), {1: ([option], "feasible")}, _config(), checker)
    assert result.J == 10.0 and result.design["test_label"] == "base"


def test_outer_improvement_timeout_retains_best_verified_design(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"base": 10.0})
    monkeypatch.setattr(outer, "_improve_incumbent", lambda plan, design, config, checker, cache, deadline:
                        ImprovementResult("timeout", design, verified=True, J=10.0))
    result = optimize_partitions(NS(), {1: ([_option("base")], "feasible")},
                                 _config(), checker)
    assert result.status == "timeout" and result.verified and result.J == 10.0
    assert result.design["test_label"] == "base"


def test_route_selector_can_verify_a_checker_invalid_f0(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"verified_variant": 7.0})
    option = _option("repaired")

    def selector(plan, design, config, checker, cache, deadline):
        candidate = _design(option)
        candidate["test_label"] = "verified_variant"
        return RouteSelectionResult("verified", candidate, True, 7.0)

    monkeypatch.setattr(outer, "select_whole_design_routes", selector)
    result = optimize_partitions(NS(), {1: ([option], "feasible")}, _config(), checker,
                                 lower_bound_hubs=1)
    assert result.status == "verified" and result.J == 7.0
    assert result.design["test_label"] == "verified_variant"


def test_provisional_result_keeps_first_hub_count(monkeypatch):
    _mock_pipeline(monkeypatch, {})
    monkeypatch.setattr(outer, "_optional_checker", lambda: None)
    batches = {1: ([_option("one")], "feasible"),
               2: ([_option("two", 2)], "feasible")}
    result = optimize_partitions(NS(), batches, _config(), lower_bound_hubs=1)
    assert result.status == "unverified" and result.design is None
    assert result.provisional_design["test_label"] == "one"


def test_failure_and_grouping_timeout_are_not_infeasibility_proofs(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {})
    batches = {1: ([_option("one")], "timeout")}
    result = optimize_partitions(NS(), batches, _config(), checker, lower_bound_hubs=1)
    assert result.status == "unverified" and result.design is None
    assert result.hub_count_status.tried == {1: "unresolved"}
    assert not result.hub_count_status.proven_minimum

    result = optimize_partitions(NS(), {1: ([], "proven_infeasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.status == "no_design_found"
    assert result.hub_count_status.tried == {1: "proven_infeasible"}


def test_expired_deadline_is_timeout_not_proof(monkeypatch):
    checker, calls = _mock_pipeline(monkeypatch, {"one": 1.0})
    result = optimize_partitions(NS(), {1: ([_option("one")], "feasible")},
                                 _config(), checker, deadline=0.0, lower_bound_hubs=1)
    assert result.status == "timeout" and result.design is None and calls == []
    assert result.hub_count_status.tried == {1: "unresolved"}


def test_configured_budget_caps_a_later_caller_deadline(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"one": 1.0})
    original = outer.construct_f0_design
    observed = []

    def f0(plan, option, config, cache, deadline):
        observed.append(deadline)
        return original(plan, option, config, cache, deadline)

    monkeypatch.setattr(outer, "construct_f0_design", f0)
    config = _config()
    config.time_budget_s = 1.0
    before = time.monotonic()
    result = optimize_partitions(NS(), {1: ([_option("one")], "feasible")},
                                 config, checker, deadline=before + 100,
                                 lower_bound_hubs=1)
    assert result.status == "verified"
    assert observed and before < observed[0] <= before + 1.01


def test_partition_input_order_does_not_change_result(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"a": 3.0, "b": 2.0, "c": 2.0})
    options = [_option("a", gap=3.0), _option("b", gap=2.0), _option("c", gap=1.0)]
    first = optimize_partitions(NS(), {1: (options, "feasible")}, _config(), checker)
    second = optimize_partitions(NS(), {1: (list(reversed(options)), "feasible")},
                                 _config(), checker)
    assert first == second and first.design["test_label"] == "c"


def test_missing_checker_yields_only_provisional_design(monkeypatch):
    # The checker is looked up in two places (outer and route_selection); switch off both, so
    # the test still means "no checker installed" once Part 2's checker exists in the project.
    monkeypatch.setattr(outer, "_optional_checker", lambda: None)
    monkeypatch.setattr(route_selection, "_optional_checker", lambda: None)
    plan, option = completed_plan()
    config = f0_config(time_budget_s=5.0, weights=NS(length=1.0, bend=0.5, crossing=5.0,
                                                    overlap=1.0),
                       limits=NS(partitions_per_H=1, beam_width=1,
                                                   hub_candidates=12, orders_kept=12,
                                                   routes_per_leg=1, exact_combo_limit=20))
    result = optimize_partitions(plan, {1: ([option], "feasible")}, config)
    assert result.status == "unverified" and not result.verified
    assert result.design is None and result.J is None
    assert result.provisional_design is not None
    assert result.hub_count_status.tried == {1: "unresolved"}


def test_real_f0_orders_and_route_selection_feed_checker():
    plan, option = completed_plan(node_point=(2.0, 2.0))
    config = f0_config(time_budget_s=5.0, weights=NS(length=1.0, bend=0.5, crossing=5.0,
                                                    overlap=1.0),
                       limits=NS(partitions_per_H=1, beam_width=2, hub_candidates=3,
                                 orders_kept=2, routes_per_leg=1, exact_combo_limit=20))
    checked = []

    def checker(plan, design, config):
        hub = design["hubs"][0]
        ring = hub["ring"]
        assert ring["sequence"] == ["H1", "N1", "H1"]
        assert len(ring["legs"]) == 2
        for leg in ring["legs"]:
            assert tuple(leg["polyline"][0]) in {tuple(hub["location"]), (2.0, 2.0)}
        checked.append(tuple(hub["location"]))
        return NS(valid=True, metrics=NS(J=2.0))

    result = optimize_partitions(plan, {1: ([option], "feasible")}, config, checker,
                                 lower_bound_hubs=1)
    assert result.status == "verified" and result.design is not None
    assert result.J == 2.0 and result.hub_count_status.proven_minimum
    assert result.configurations_considered >= 2 and len(set(checked)) >= 2


def test_invalid_part1_proof_and_limits_are_rejected():
    with pytest.raises(ValueError):
        optimize_partitions(NS(), {1: ([_option("x")], "proven_infeasible")},
                            _config(), lower_bound_hubs=1)
    with pytest.raises(ValueError):
        optimize_partitions(NS(), {1: ([], "feasible")},
                            NS(limits=NS(partitions_per_H=0, beam_width=1), time_budget_s=1))
