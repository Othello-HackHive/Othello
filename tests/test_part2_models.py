"""T02: Contract 2 models (Design) and result models."""

import pytest
from pydantic import ValidationError

from othello.models.design import Design, Hub, Ring, RouteLeg
from othello.models.result import (
    VIOLATION_CODES,
    HubCountStatus,
    Metrics,
    RuleCheck,
    SolveResult,
    ValidationResult,
    Violation,
)


def _sample_design() -> Design:
    """H1 serves N1, N3, N2 (the abridged example from slide 8)."""
    legs = [
        RouteLeg(from_id="H1", to_id="N1", polyline=[(5.2, 3.6), (7.6, 3.6), (7.6, 1.2)]),
        RouteLeg(from_id="N1", to_id="N3", polyline=[(7.6, 1.2), (6.6, 1.2), (6.6, 2.2), (1.5, 2.2)], doors_used=["D1"]),
        RouteLeg(from_id="N3", to_id="N2", polyline=[(1.5, 2.2), (1.5, 5.0)]),
        RouteLeg(from_id="N2", to_id="H1", polyline=[(1.5, 5.0), (5.2, 5.0), (5.2, 3.6)]),
    ]
    ring = Ring(hub_id="H1", sequence=["H1", "N1", "N3", "N2", "H1"], legs=legs)
    hub = Hub(
        hub_id="H1",
        room_id="R05",
        location=(5.2, 3.6),
        assigned_rooms=["R05", "R01", "R04"],
        assigned_nodes=["N1", "N2", "N3"],
        ring=ring,
    )
    return Design(plan_id="PLAN_0123", hubs=[hub])


def test_design_round_trips_through_json():
    design = _sample_design()
    again = Design.model_validate_json(design.model_dump_json())
    assert again == design
    assert again.hubs[0].ring.sequence[0] == again.hubs[0].ring.sequence[-1] == "H1"


def test_design_keeps_internal_notes_separate():
    design = _sample_design()
    design.solver_notes["runtime_s"] = 1.5
    assert design.model_dump()["solver_notes"] == {"runtime_s": 1.5}


def test_leg_rejects_single_point_polyline():
    with pytest.raises(ValidationError):
        RouteLeg(from_id="H1", to_id="N1", polyline=[(0.0, 0.0)])


def test_leg_rejects_empty_polyline():
    with pytest.raises(ValidationError):
        RouteLeg(from_id="H1", to_id="N1", polyline=[])


def test_leg_doors_default_is_empty_and_not_shared():
    a = RouteLeg(from_id="H1", to_id="N1", polyline=[(0, 0), (1, 0)])
    b = RouteLeg(from_id="H1", to_id="N2", polyline=[(0, 0), (0, 1)])
    a.doors_used.append("D1")
    assert b.doors_used == []


def test_violation_codes_are_the_nineteen_official_ones():
    assert len(VIOLATION_CODES) == 19
    assert len(set(VIOLATION_CODES)) == 19
    for code in ("WALL_INTERSECTION", "WINDOW_INTERSECTION", "OUTSIDE_INNER", "CAPACITY_NODES", "ZONE_DISCONNECTED"):
        assert code in VIOLATION_CODES


def test_crossings_and_overlaps_are_not_violation_codes():
    joined = " ".join(VIOLATION_CODES)
    assert "CROSS" not in joined
    assert "OVERLAP" not in joined


def test_violation_can_carry_a_point_for_the_overlay():
    v = Violation(type="WALL_INTERSECTION", hub_id="H2", ring_id="H2", segment_index=3, point=(4.1, 2.0))
    assert v.point == (4.1, 2.0)
    assert Violation(type="COVERAGE_MISSING").hub_id is None


def test_validation_result_invalid_without_metrics_is_allowed():
    result = ValidationResult(valid=False, violations=[Violation(type="RING_NOT_CLOSED", hub_id="H1")])
    assert not result.valid and result.metrics is None and result.checks == []


def test_validation_result_with_metrics_and_checks():
    metrics = Metrics(hub_count=2, L=49.0, X=0, O=0.0, B=20, J=59.0, per_hub={"H1": {"area_m2": 71.0}}, area_gap_m2=24.0)
    checks = [RuleCheck(rule_id="capacity", label="CAPACITY", passed=True, value="3/5 - 71.0/100")]
    result = ValidationResult(valid=True, metrics=metrics, checks=checks)
    assert result.metrics.J == 59.0 and result.checks[0].passed


def test_hub_count_status_accepts_only_known_labels():
    status = HubCountStatus(lower_bound=2, tried={2: "feasible"}, proven_minimum=True)
    assert status.tried[2] == "feasible"
    with pytest.raises(ValidationError):
        HubCountStatus(lower_bound=2, tried={2: "maybe"})


def test_solve_result_failure_has_no_design():
    result = SolveResult(status="no_design_found", notes=["timeout"])
    assert result.design is None and result.validation is None and result.notes == ["timeout"]


def test_solve_result_rejects_unknown_status():
    with pytest.raises(ValidationError):
        SolveResult(status="great")


def test_solve_result_holds_a_verified_design():
    design = _sample_design()
    ok = ValidationResult(valid=True)
    result = SolveResult(status="ok", design=design, validation=ok, lower_bound_hubs=1, runtime_s=0.2)
    assert result.design.plan_id == "PLAN_0123" and result.validation.valid
