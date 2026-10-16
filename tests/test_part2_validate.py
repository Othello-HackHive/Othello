"""T08: the report card (check_design / score_design)."""

import pytest
from fixtures import designs
from fixtures.plans import SLIDE5_HUB_AREAS, slide5_plan, tiny_plan

from othello.checker import check_design, coerce_design, is_valid, score_design
from othello.checker.metrics import is_better
from othello.checker.rules import RULES
from othello.models.config import SolverConfig
from othello.models.design import Design
from othello.models.result import ValidationResult

CFG = SolverConfig()
RULE_IDS = [rule.rule_id for rule in RULES]


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


@pytest.fixture(scope="module")
def tiny():
    return tiny_plan()


def line(result, rule_id):
    return next(c for c in result.checks if c.rule_id == rule_id)


# ------------------------------------------------------------ good plans


def test_good_plan_is_valid_with_a_full_report(plan):
    result = check_design(plan, designs.slide5_design())
    assert result.valid and result.violations == []
    assert result.metrics.hub_count == 2
    assert result.metrics.J == pytest.approx(64.8)
    assert [c.rule_id for c in result.checks] == RULE_IDS
    assert all(c.passed for c in result.checks)


def test_good_tiny_plan_is_valid(tiny):
    result = check_design(tiny, designs.tiny_design())
    assert result.valid and result.metrics.J == pytest.approx(13.0)


@pytest.mark.parametrize("build", [designs.slide5_with_crossing, designs.slide5_with_overlap])
def test_costly_plans_are_still_valid_and_cost_more(plan, build):
    base = check_design(plan, designs.slide5_design())
    result = check_design(plan, build())
    assert result.valid
    assert result.metrics.J > base.metrics.J
    assert is_better(base.metrics, result.metrics)


def test_pass_fail_lines_read_nicely(plan):
    result = check_design(plan, designs.slide5_design())
    assert line(result, "coverage").value == "6/6 ROOMS · 6/6 NODES"
    assert line(result, "capacity").value == "3/5 NODES · 71.0/100 M2"
    assert line(result, "zones").value == "ALL CONNECTED"
    assert line(result, "rings").value == "2/2 CLOSED"
    assert line(result, "free_space").value == "CLEAR"
    assert line(result, "doors").value == "ONLY VIA DOORS"
    assert line(result, "schema").value == "OK"
    assert line(result, "nodes").value == "ON POINT"
    assert line(result, "hub_points").value.endswith("M FROM WALLS")
    assert all(c.label and c.detail for c in result.checks)


def test_is_valid_shortcut(plan):
    assert is_valid(plan, designs.slide5_design())
    assert not is_valid(plan, designs.broken_missing_node())


# ---------------------------------------------------------- score_design


def test_score_design_uses_hub_areas_from_the_plan(plan):
    metrics = score_design(plan, designs.slide5_design())
    assert metrics.area_gap_m2 == pytest.approx(24.0)
    assert metrics.per_hub["H1"]["area_m2"] == SLIDE5_HUB_AREAS["H1"]
    assert metrics.per_hub["H2"]["area_m2"] == SLIDE5_HUB_AREAS["H2"]
    assert (metrics.L, metrics.X, metrics.O, metrics.B) == (
        pytest.approx(50.8),
        0,
        pytest.approx(0.0),
        28,
    )


def test_score_design_honours_the_weights(plan):
    heavy = SolverConfig(weights={"length": 1.0, "crossing": 5.0, "overlap": 1.0, "bend": 2.0})
    assert score_design(plan, designs.slide5_design(), heavy).J == pytest.approx(50.8 + 2.0 * 28)


def test_score_matches_the_report_card(plan):
    design = designs.slide5_with_crossing()
    assert score_design(plan, design) == check_design(plan, design).metrics


# ---------------------------------------------------------- broken plans


@pytest.mark.parametrize("name", sorted(designs.ALL_BROKEN))
def test_broken_plan_is_invalid_with_a_failing_line_and_scores(plan, name):
    result = check_design(plan, designs.ALL_BROKEN[name]())
    assert not result.valid
    assert result.violations
    assert any(not c.passed for c in result.checks)
    assert result.metrics is not None  # scores are still worked out


def test_failing_lines_match_the_violations(plan):
    result = check_design(plan, designs.broken_missing_node())
    failed = {c.rule_id for c in result.checks if not c.passed}
    assert failed == {"rings"}
    assert line(result, "rings").value == "1/2 CLOSED"


def test_hub_in_wall_fails_two_rules_and_shows_zero_clearance(plan):
    result = check_design(plan, designs.broken_hub_in_wall())
    failed = {c.rule_id for c in result.checks if not c.passed}
    assert {"hub_points", "free_space"} <= failed
    assert line(result, "hub_points").value.startswith("0.00 M")


def test_capacity_line_shows_the_overload(plan):
    result = check_design(plan, designs.broken_capacity_all_in_one_hub())
    assert not line(result, "capacity").passed
    assert line(result, "capacity").value == "6/5 NODES · 118.0/100 M2"


def test_coverage_line_shows_what_is_missing(plan):
    result = check_design(plan, designs.broken_coverage_missing())
    assert line(result, "coverage").value == "5/6 ROOMS · 5/6 NODES"


def test_config_can_make_a_good_plan_invalid(plan):
    strict = SolverConfig(max_nodes_per_hub=2)
    result = check_design(plan, designs.slide5_design(), strict)
    assert not result.valid
    assert line(result, "capacity").value.startswith("3/2 NODES")


# ------------------------------------------------------------ odd inputs


def test_a_design_with_no_hubs_is_invalid_and_does_not_crash(plan):
    result = check_design(plan, Design(plan_id=plan.plan_id, hubs=[]))
    assert not result.valid
    assert not line(result, "coverage").passed
    assert result.metrics.hub_count == 0 and result.metrics.J == 0.0
    assert line(result, "hub_points").value == "NO HUBS"


def test_a_plain_dictionary_in_the_design_shape_is_accepted_like_a_design(plan):
    design = designs.slide5_design()
    as_dict = design.model_dump()  # what the solver hands over: nested dictionaries and lists
    assert check_design(plan, as_dict) == check_design(plan, design)
    assert score_design(plan, as_dict) == score_design(plan, design)
    assert check_design(plan, as_dict).valid


def test_a_broken_dictionary_is_judged_invalid_with_a_clear_reason_not_a_crash(plan):
    as_dict = designs.broken_cable_through_wall().model_dump()
    result = check_design(plan, as_dict)
    assert not result.valid and "WALL_INTERSECTION" in {v.type for v in result.violations}


@pytest.mark.parametrize(
    "garbage",
    [None, 5, "design", [], {}, {"plan_id": "x"}, {"plan_id": "x", "hubs": "none"}, {"plan_id": "x", "hubs": [{"hub_id": "H1"}]}],
)
def test_something_that_is_not_a_design_is_invalid_not_an_exception(plan, garbage):
    result = check_design(plan, garbage)
    assert not result.valid and result.metrics is None
    assert [v.type for v in result.violations] == ["OUTPUT_SCHEMA"]
    assert [c.rule_id for c in result.checks] == ["schema"] and not result.checks[0].passed


def test_scoring_garbage_raises_a_plain_value_error(plan):
    with pytest.raises(ValueError, match="Design"):
        score_design(plan, "design")
    with pytest.raises(ValueError, match="Design shape"):
        score_design(plan, {"plan_id": "x"})


def test_coerce_design_returns_a_design_unchanged_and_converts_a_dictionary():
    design = designs.slide5_design()
    assert coerce_design(design) is design
    assert coerce_design(design.model_dump()) == design


def test_result_can_be_saved_and_loaded_again(plan):
    result = check_design(plan, designs.broken_cable_through_wall())
    again = ValidationResult.model_validate_json(result.model_dump_json())
    assert again == result


def test_violations_carry_the_hub_and_spot_for_the_overlay(plan):
    result = check_design(plan, designs.broken_cable_through_wall())
    wall = next(v for v in result.violations if v.type == "WALL_INTERSECTION")
    assert wall.hub_id == "H1" and wall.ring_id == "H1" and wall.point is not None
