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
from othello.solver.tests._doubles import make_opening
from othello.solver.hub_candidates import HubCandidateLimitError


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
    monkeypatch.setattr(outer, "construct_quick_f0_design", lambda *args:
                        F0Result("unreachable_on_grid", diagnostics=("quick probe exhausted",)))
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


def test_every_quick_partition_runs_before_deep_search(monkeypatch):
    events = []

    def quick(plan, option, config, cache, deadline):
        events.append(f"quick:{option.label}")
        return F0Result("constructed_unverified", _design(option))

    def full(plan, option, config, cache, deadline):
        events.append(f"full:{option.label}")
        return F0Result("constructed_unverified", _design(option))

    monkeypatch.setattr(outer, "construct_quick_f0_design", quick)
    monkeypatch.setattr(outer, "construct_f0_design", full)
    monkeypatch.setattr(outer, "select_whole_design_routes", lambda *args:
                        RouteSelectionResult("no_verified_design"))
    monkeypatch.setattr(outer, "_configuration_designs", lambda *args: ())
    monkeypatch.setattr(outer, "_improve_incumbent", lambda *args:
                        ImprovementResult("unchanged", args[1], verified=True, J=1.0))

    def checker(plan, design, config):
        return NS(valid=True, metrics=NS(J=1.0))

    optimize_partitions(NS(), {1: ([_option("a"), _option("b")], "feasible")},
                        _config(), checker, lower_bound_hubs=1)
    assert events == ["quick:a", "quick:b", "full:a", "full:b"]


def test_improvement_repeats_only_after_a_better_complete_design(monkeypatch):
    from copy import deepcopy

    checker, _ = _mock_pipeline(monkeypatch, {"a": 10.0})
    seen = []

    def improve(plan, design, config, injected_checker, cache, deadline):
        seen.append(outer._design_key(design))
        if len(seen) == 1:
            better = deepcopy(design)
            leg = better["hubs"][0]["ring"]["legs"][0]
            leg["polyline"] = [leg["polyline"][0], (1.0, 1.0), leg["polyline"][-1]]
            return ImprovementResult("improved", better, verified=True, J=5.0)
        return ImprovementResult("unchanged", design, verified=True, J=5.0)

    monkeypatch.setattr(outer, "_improve_incumbent", improve)
    result = optimize_partitions(NS(), {1: ([_option("a")], "feasible")},
                                 _config(), checker, lower_bound_hubs=1)
    assert len(seen) == 2
    assert result.J == 5.0


def test_improvement_sweeps_memoize_well_formed_rejections(monkeypatch):
    from copy import deepcopy

    _mock_pipeline(monkeypatch, {"a": 10.0})
    rejected_checks = 0
    sweeps = 0

    def checker(plan, design, config):
        nonlocal rejected_checks
        polyline = design["hubs"][0]["ring"]["legs"][0]["polyline"]
        if len(polyline) == 3 and polyline[1] == (9.0, 9.0):
            rejected_checks += 1
            return NS(valid=False, metrics=None)
        return NS(valid=True, metrics=NS(J=5.0 if len(polyline) == 3 else 10.0))

    def improve(plan, design, config, injected_checker, cache, deadline):
        nonlocal sweeps
        sweeps += 1
        rejected = deepcopy(design)
        leg = rejected["hubs"][0]["ring"]["legs"][0]
        leg["polyline"] = [leg["polyline"][0], (9.0, 9.0), leg["polyline"][-1]]
        assert not injected_checker(plan, rejected, config).valid
        if sweeps == 1:
            better = deepcopy(design)
            leg = better["hubs"][0]["ring"]["legs"][0]
            leg["polyline"] = [leg["polyline"][0], (1.0, 1.0), leg["polyline"][-1]]
            return ImprovementResult("improved", better, verified=True, J=5.0)
        return ImprovementResult("unchanged", design, verified=True, J=5.0)

    monkeypatch.setattr(outer, "_improve_incumbent", improve)
    result = optimize_partitions(NS(), {1: ([_option("a")], "feasible")},
                                 _config(), checker, lower_bound_hubs=1)
    assert result.J == 5.0 and sweeps == 2 and rejected_checks == 1


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


def test_checker_exception_after_valid_incumbent_preserves_ranking_and_recovers(monkeypatch):
    ordinary, calls = _mock_pipeline(monkeypatch, {"first": 8.0, "bad": None, "better": 3.0})

    def checker(plan, design, config):
        if design["test_label"] == "bad":
            raise RuntimeError("intermittent checker failure")
        return ordinary(plan, design, config)

    options = [_option("first", gap=0.0), _option("bad", gap=1.0),
               _option("better", gap=2.0)]
    result = optimize_partitions(NS(), {1: (options, "feasible")}, _config(), checker,
                                 lower_bound_hubs=1)
    assert result.status == "verified" and result.verified
    assert result.design["test_label"] == "better" and result.J == 3.0
    assert calls == ["first", "bad", "better"]
    assert any("RuntimeError" in detail and "intermittent checker failure" in detail
               for detail in result.diagnostics)


@pytest.mark.parametrize("malformed", [None, NS(valid=True, metrics=None), NS(valid=True, metrics=NS(J=-1.0))])
def test_malformed_checker_result_is_diagnostic_and_later_partition_can_verify(monkeypatch, malformed):
    ordinary, _ = _mock_pipeline(monkeypatch, {"bad": None, "good": 4.0})

    def checker(plan, design, config):
        return malformed if design["test_label"] == "bad" else ordinary(plan, design, config)

    result = optimize_partitions(NS(), {1: ([_option("bad", gap=0), _option("good", gap=1)],
                                           "feasible")}, _config(), checker, lower_bound_hubs=1)
    assert result.status == "verified" and result.design["test_label"] == "good"
    assert result.J == 4.0 and any("checker failed" in detail for detail in result.diagnostics)


def test_all_malformed_checker_responses_leave_only_a_provisional_design(monkeypatch):
    _mock_pipeline(monkeypatch, {"bad": None})
    result = optimize_partitions(NS(), {1: ([_option("bad")], "feasible")}, _config(),
                                 checker=lambda *_: NS(valid=True, metrics=NS(J=float("nan"))),
                                 lower_bound_hubs=1)
    assert result.status == "unverified" and not result.verified
    assert result.design is None and result.J is None
    assert result.provisional_design is not None
    assert result.hub_count_status.tried == {1: "unresolved"}
    assert any("checker failed" in detail for detail in result.diagnostics)


def test_candidate_limit_failure_in_f0_is_not_an_outer_crash():
    plan, option = completed_plan()
    plan.openings["LEFT"] = make_opening("LEFT", [(0, 1.5), (0, 2.5)])
    plan.openings["RIGHT"] = make_opening("RIGHT", [(4, 1.5), (4, 2.5)])
    cfg = f0_config(time_budget_s=5.0,
                    limits=NS(partitions_per_H=1, beam_width=1, hub_candidates=1,
                              orders_kept=2, routes_per_leg=1, exact_combo_limit=20))
    result = optimize_partitions(plan, {1: ([option], "feasible")}, cfg,
                                 checker=lambda *_: NS(valid=True, metrics=NS(J=1.0)),
                                 lower_bound_hubs=1)
    assert result.status == "candidate_configuration_error" and not result.verified
    assert result.design is None and result.J is None
    assert result.hub_count_status.tried == {1: "unresolved"}
    assert any("candidate_configuration_error" in detail and "hub_candidates=1" in detail
               for detail in result.diagnostics)


def test_candidate_limit_failure_can_be_followed_by_a_valid_partition(monkeypatch):
    checker, calls = _mock_pipeline(monkeypatch, {"bad": None, "good": 2.0},
                                    {"bad": "candidate_configuration_error"})
    result = optimize_partitions(NS(), {1: ([_option("bad", gap=0), _option("good", gap=1)],
                                           "feasible")}, _config(), checker, lower_bound_hubs=1)
    assert result.status == "verified" and result.design["test_label"] == "good"
    assert result.J == 2.0 and calls == ["bad", "good"]
    assert any("candidate_configuration_error" in detail for detail in result.diagnostics)


def test_candidate_limit_failure_during_configuration_search_preserves_incumbent(monkeypatch):
    original_configurations = outer._configuration_designs
    checker, _ = _mock_pipeline(monkeypatch, {"good": 2.0})

    def too_small(*args):
        raise HubCandidateLimitError("limits.hub_candidates=1 is below the 2 distinct required representatives")

    monkeypatch.setattr(outer, "generate_hub_candidates", too_small)
    monkeypatch.setattr(outer, "_configuration_designs", original_configurations)
    result = optimize_partitions(NS(), {1: ([_option("good")], "feasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.status == "verified" and result.design["test_label"] == "good"
    assert result.J == 2.0
    assert any("candidate_configuration_error" in detail and "hub_candidates=1" in detail
               for detail in result.diagnostics)


def test_checker_exception_on_alternative_does_not_discard_baseline(monkeypatch):
    ordinary, _ = _mock_pipeline(monkeypatch, {"good": 2.0, "bad": None})
    alternative = _design(_option("bad"))
    alternative["hubs"][0]["ring"]["legs"][0]["polyline"] = [
        (1.0, 0.0), (1.0, 1.0), (2.0, 1.0), (2.0, 0.0),
    ]
    monkeypatch.setattr(outer, "_configuration_designs",
                        lambda *args: iter((alternative,)))

    def checker(plan, design, config):
        if design["test_label"] == "bad":
            raise RuntimeError("alternative checker failed")
        return ordinary(plan, design, config)

    result = optimize_partitions(NS(), {1: ([_option("good")], "feasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.status == "verified" and result.design["test_label"] == "good"
    assert result.J == 2.0
    assert any("alternative" in detail and "RuntimeError" in detail for detail in result.diagnostics)


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
    assert result.timeout_with_verified_design
    assert result.submission_design == result.design


def test_quick_checker_failure_recovers_through_comprehensive_f0(monkeypatch):
    checker, calls = _mock_pipeline(monkeypatch, {"full": 4.0})
    quick = _design(_option("quick"))
    monkeypatch.setattr(outer, "construct_quick_f0_design", lambda *args:
                        F0Result("constructed_unverified", quick))
    result = optimize_partitions(NS(), {1: ([_option("full")], "feasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.verified and result.design["test_label"] == "full" and result.J == 4.0
    assert calls == ["full"]
    assert result.submission_design == result.design


def test_quick_verified_incumbent_survives_full_failure_and_timeout(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"quick": 3.0})
    quick = _design(_option("quick"))
    monkeypatch.setattr(outer, "construct_quick_f0_design", lambda *args:
                        F0Result("constructed_unverified", quick))
    monkeypatch.setattr(outer, "construct_f0_design", lambda *args:
                        F0Result("timeout", diagnostics=("full search expired",)))
    result = optimize_partitions(NS(), {1: ([_option("full")], "feasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.status == "timeout" and result.timeout_with_verified_design
    assert result.submission_design["test_label"] == "quick" and result.J == 3.0


def test_quick_verified_incumbent_is_not_replaced_by_worse_full_design(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"quick": 3.0, "full": 9.0})
    quick = _design(_option("quick"))
    monkeypatch.setattr(outer, "construct_quick_f0_design", lambda *args:
                        F0Result("constructed_unverified", quick))
    result = optimize_partitions(NS(), {1: ([_option("full")], "feasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.status == "verified" and result.J == 3.0
    assert result.submission_design["test_label"] == "quick"


def test_quick_provisional_timeout_is_not_submittable(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {})
    quick = _design(_option("quick"))
    monkeypatch.setattr(outer, "construct_quick_f0_design", lambda *args:
                        F0Result("constructed_unverified", quick))
    monkeypatch.setattr(outer, "construct_f0_design", lambda *args: F0Result("timeout"))
    result = optimize_partitions(NS(), {1: ([_option("full")], "feasible")}, _config(),
                                 checker, lower_bound_hubs=1)
    assert result.status == "timeout" and not result.verified
    assert result.design is None and result.J is None and result.submission_design is None
    assert result.provisional_design == quick and not result.timeout_with_verified_design


def test_final_reserve_shortens_only_configured_search_budget(monkeypatch):
    checker, _ = _mock_pipeline(monkeypatch, {"one": 1.0})
    observed = []
    monkeypatch.setattr(outer, "construct_quick_f0_design", lambda plan, option, config, cache, deadline:
                        observed.append(deadline) or F0Result("unreachable_on_grid"))
    config = _config()
    config.time_budget_s = 2.0
    config.final_check_reserve_fraction = 0.25
    before = time.monotonic()
    optimize_partitions(NS(), {1: ([_option("one")], "feasible")}, config, checker)
    assert observed and before + 1.45 <= observed[0] <= before + 1.60


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
