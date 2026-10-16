"""T09: the official answer file (slide 8 format)."""

import itertools
import json
import math

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker import check_design, score_design
from othello.io.official import (
    HUB_KEYS,
    METRIC_KEYS,
    SEGMENT_KEYS,
    TOP_KEYS,
    UnverifiedDesignError,
    build_official,
    design_from_official,
    from_output_coords,
    official_schema_problems,
    round_design,
    to_output_coords,
    write_official_json,
)
from othello.models.config import SolverConfig
from othello.models.design import Design

CFG = SolverConfig()


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


@pytest.fixture(scope="module")
def tiny():
    return tiny_plan()


def noisy(design: Design, dx: float = 0.00001234, dy: float = -0.00000777) -> Design:
    """The same design with tiny numeric noise on every coordinate (far below 1 mm)."""
    copy = design.model_copy(deep=True)
    for hub in copy.hubs:
        hub.location = (hub.location[0] + dx, hub.location[1] + dy)
        for leg in hub.ring.legs:
            leg.polyline = [(x + dx, y + dy) for x, y in leg.polyline]
    return copy


# ------------------------------------------------------------- the shape


def test_official_dictionary_has_exactly_the_slide_8_fields(plan):
    data = build_official(plan, designs.slide5_design())
    assert tuple(data) == TOP_KEYS
    assert data["plan_id"] == "SLIDE5_PLAN" and data["units"] == "m"
    assert len(data["hubs"]) == 2
    for hub in data["hubs"]:
        assert tuple(hub) == HUB_KEYS
        assert set(hub["location"]) == {"x", "y"}
        for segment in hub["route_segments"]:
            assert tuple(segment) == SEGMENT_KEYS
    assert tuple(data["reported_metrics"]) == METRIC_KEYS


def test_official_dictionary_has_no_format_problems(plan):
    assert official_schema_problems(build_official(plan, designs.slide5_design())) == []


def test_sequences_segments_and_lengths_agree(plan):
    data = build_official(plan, designs.slide5_design())
    h1 = data["hubs"][0]
    assert h1["hub_id"] == "H1" and h1["room_id"] == "R05"
    assert h1["closed_sequence"] == ["H1", "N5", "N4", "N1", "H1"]
    assert [(s["from"], s["to"]) for s in h1["route_segments"]] == [
        ("H1", "N5"),
        ("N5", "N4"),
        ("N4", "N1"),
        ("N1", "H1"),
    ]
    for segment in h1["route_segments"]:
        pts = segment["polyline"]
        walked = sum(math.dist(a, b) for a, b in itertools.pairwise(pts))
        assert segment["length_m"] == pytest.approx(walked, abs=1e-6)
    assert h1["ring_length_m"] == pytest.approx(27.2)
    assert data["hubs"][1]["ring_length_m"] == pytest.approx(23.6)
    assert data["reported_metrics"] == {"hub_count": 2, "total_length_m": pytest.approx(50.8)}


def test_the_return_leg_is_included_and_chained(plan):
    for hub in build_official(plan, designs.slide5_design())["hubs"]:
        segments = hub["route_segments"]
        assert segments[-1]["to"] == hub["hub_id"]
        assert segments[0]["polyline"][0] == [hub["location"]["x"], hub["location"]["y"]]
        for a, b in itertools.pairwise(segments):
            assert a["polyline"][-1] == b["polyline"][0]


# ---------------------------------------------------------- writing a file


def test_a_valid_design_is_written_and_reads_back(plan, tmp_path):
    path = tmp_path / "out" / "nested" / "SLIDE5_PLAN.json"
    result = write_official_json(plan, designs.slide5_design(), path)
    assert result.valid
    data = json.loads(path.read_text(encoding="utf-8"))
    assert official_schema_problems(data) == []
    assert data == build_official(plan, designs.slide5_design())
    assert [p.name for p in path.parent.iterdir()] == ["SLIDE5_PLAN.json"]  # no temp files left


def test_tiny_design_is_written(tiny, tmp_path):
    path = tmp_path / "tiny.json"
    write_official_json(tiny, designs.tiny_design(), path)
    assert json.loads(path.read_text())["reported_metrics"]["total_length_m"] == pytest.approx(10.0)


@pytest.mark.parametrize("build", [designs.slide5_with_crossing, designs.slide5_with_overlap])
def test_costly_but_valid_designs_are_written(plan, tmp_path, build):
    path = tmp_path / "x.json"
    assert write_official_json(plan, build(), path).valid
    assert path.exists()


@pytest.mark.parametrize("name", sorted(designs.ALL_BROKEN))
def test_every_broken_design_is_refused_and_nothing_is_written(plan, tmp_path, name):
    path = tmp_path / "answer.json"
    with pytest.raises(UnverifiedDesignError) as caught:
        write_official_json(plan, designs.ALL_BROKEN[name](), path)
    assert caught.value.violations
    assert not path.exists()
    assert list(tmp_path.iterdir()) == []  # not even a temp file


def test_a_refused_design_leaves_an_earlier_good_answer_untouched(plan, tmp_path):
    path = tmp_path / "answer.json"
    write_official_json(plan, designs.slide5_design(), path)
    before = path.read_bytes()
    with pytest.raises(UnverifiedDesignError):
        write_official_json(plan, designs.broken_cable_through_wall(), path)
    assert path.read_bytes() == before
    assert [p.name for p in tmp_path.iterdir()] == ["answer.json"]


def test_the_error_message_names_the_broken_rules(plan, tmp_path):
    with pytest.raises(UnverifiedDesignError, match="WALL_INTERSECTION"):
        write_official_json(plan, designs.broken_cable_through_wall(), tmp_path / "a.json")


def test_config_can_make_a_good_design_refused(plan, tmp_path):
    with pytest.raises(UnverifiedDesignError, match="CAPACITY_NODES"):
        write_official_json(plan, designs.slide5_design(), tmp_path / "a.json", SolverConfig(max_nodes_per_hub=2))


# ----------------------------------------------------------- rounding


def test_numbers_are_rounded_to_the_configured_decimals(plan):
    data = build_official(plan, noisy(designs.slide5_design()))
    for hub in data["hubs"]:
        for value in (hub["location"]["x"], hub["location"]["y"], hub["ring_length_m"]):
            assert round(value, 4) == value
        for segment in hub["route_segments"]:
            assert round(segment["length_m"], 4) == segment["length_m"]
            for x, y in segment["polyline"]:
                assert round(x, 4) == x and round(y, 4) == y


def test_noisy_design_is_still_written_and_stays_valid(plan, tmp_path):
    path = tmp_path / "noisy.json"
    write_official_json(plan, noisy(designs.slide5_design()), path)
    back = design_from_official(plan, json.loads(path.read_text()))
    assert check_design(plan, back).valid


def test_rounding_uses_the_same_rule_everywhere():
    design = designs.slide5_design()
    rounded = round_design(noisy(design), 4)
    hub = rounded.hubs[0]
    assert hub.location == hub.ring.legs[0].polyline[0] == hub.ring.legs[-1].polyline[-1]
    assert not any(x == 0.0 and math.copysign(1, x) < 0 for leg in hub.ring.legs for x, _ in leg.polyline)


def test_precision_follows_the_config(plan):
    coarse = SolverConfig(output_precision_decimals=2)
    data = build_official(plan, noisy(designs.slide5_design()), coarse)
    assert data["hubs"][0]["location"] == {"x": 5.0, "y": 3.5}


# ------------------------------------------- the organisers' view: the file


def test_reading_the_file_back_gives_the_same_scores(plan, tmp_path):
    original = designs.slide5_design()
    path = tmp_path / "a.json"
    write_official_json(plan, original, path)
    rebuilt = design_from_official(plan, json.loads(path.read_text()))
    assert check_design(plan, rebuilt).valid
    mine, theirs = score_design(plan, original), score_design(plan, rebuilt)
    assert theirs.J == pytest.approx(mine.J) == pytest.approx(64.8)
    assert (theirs.hub_count, theirs.X, theirs.B) == (mine.hub_count, mine.X, mine.B)


def test_scores_come_from_the_lines_not_from_reported_numbers(plan):
    data = build_official(plan, designs.slide5_design())
    data["reported_metrics"] = {"hub_count": 1, "total_length_m": 1.0}  # lies
    data["hubs"][0]["ring_length_m"] = 0.5
    assert score_design(plan, design_from_official(plan, data)).J == pytest.approx(64.8)


# ------------------------------------------------------------ transform


def test_default_transform_changes_nothing(plan):
    assert to_output_coords(plan, (5.2, 3.6)) == (5.2, 3.6)
    assert from_output_coords(plan, (5.2, 3.6)) == (5.2, 3.6)


def test_shift_and_flip_are_exact_inverses():
    plan = slide5_plan().model_copy(update={"output_transform": {"dx": 1.5, "dy": 9.0, "flip_y": True}})
    out = to_output_coords(plan, (2.0, 3.0))
    assert out == (3.5, 6.0)  # x + 1.5, then -y + 9
    assert from_output_coords(plan, out) == (2.0, 3.0)


def test_output_uses_the_transform_and_reads_back_to_the_same_design(tmp_path):
    base = slide5_plan()
    shifted = base.model_copy(update={"output_transform": {"dx": 10.0, "dy": 0.0, "flip_y": False}})
    path = tmp_path / "shifted.json"
    write_official_json(shifted, designs.slide5_design(), path)
    data = json.loads(path.read_text())
    assert data["hubs"][0]["location"] == {"x": 15.0, "y": 3.5}  # H1 was at (5.0, 3.5)
    assert check_design(shifted, design_from_official(shifted, data)).valid


# ------------------------------------------------------ format checking


def good_dict(plan):
    return build_official(plan, designs.slide5_design())


def test_schema_check_catches_a_missing_field(plan):
    data = good_dict(plan)
    del data["hubs"][0]["ring_length_m"]
    assert any("missing ring_length_m" in p for p in official_schema_problems(data))


def test_schema_check_catches_an_extra_field(plan):
    data = good_dict(plan)
    data["debug"] = True
    data["hubs"][1]["color"] = "#fff"
    problems = official_schema_problems(data)
    assert any("unexpected field debug" in p for p in problems)
    assert any("unexpected field color" in p for p in problems)


def test_schema_check_catches_wrong_units_and_types(plan):
    data = good_dict(plan)
    data["units"] = "mm"
    data["hubs"][0]["location"] = {"x": "5", "y": 3}
    data["hubs"][0]["route_segments"][0]["polyline"] = [[1, 2]]
    problems = " | ".join(official_schema_problems(data))
    assert 'units must be "m"' in problems
    assert "location must be" in problems
    assert "at least two" in problems


def test_schema_check_catches_an_open_ring_and_wrong_leg_count(plan):
    data = good_dict(plan)
    data["hubs"][0]["closed_sequence"][-1] = "N1"
    data["hubs"][1]["route_segments"].pop()
    problems = " | ".join(official_schema_problems(data))
    assert "must start and end at H1" in problems
    assert "needs 4 legs, has 3" in problems


def test_schema_check_catches_a_hub_count_mismatch_and_non_objects(plan):
    data = good_dict(plan)
    data["reported_metrics"]["hub_count"] = 5
    assert any("does not match" in p for p in official_schema_problems(data))
    assert official_schema_problems([]) == ["top level is not an object"]
    assert official_schema_problems({"hubs": "x"})  # lots of problems, never a crash
