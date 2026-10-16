"""T14: the overlay JSON (the picture data for the frontend)."""

import json
import math

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker import check_design
from othello.checker.metrics import (
    bend_points_closed,
    compute_metrics,
    crossing_points,
    design_markers,
    overlap_stretches,
)
from othello.io.errors import OutputWriteError
from othello.io.official import build_official
from othello.io.overlay import (
    OVERLAY_KEYS,
    SCHEMA_VERSION,
    build_overlay,
    overlay_schema_problems,
    write_overlay_json,
)
from othello.models.config import SolverConfig
from othello.models.result import HubCountStatus


@pytest.fixture(scope="module")
def plan():
    return slide5_plan()


@pytest.fixture(scope="module")
def good(plan):
    design = designs.slide5_design()
    return build_overlay(plan, design, check_design(plan, design))


def overlay_for(plan, design, **options):
    return build_overlay(plan, design, check_design(plan, design), **options)


def all_points(value):
    """Every [x, y] pair anywhere inside a nested structure."""
    if isinstance(value, list):
        if len(value) == 2 and all(isinstance(v, int | float) and not isinstance(v, bool) for v in value):
            yield value
        else:
            for item in value:
                yield from all_points(item)
    elif isinstance(value, dict):
        for item in value.values():
            yield from all_points(item)


# ------------------------------------------------------ where the costs are


def test_bend_points_are_the_corners_of_the_ring():
    ring = [(0, 0), (4, 0), (4, 3), (0, 3), (0, 0)]
    assert sorted(bend_points_closed(ring, 0.001)) == [(0, 0), (0, 3), (4, 0), (4, 3)]
    assert bend_points_closed([(0, 0), (2, 0), (5, 0)], 0.001) == []


def test_crossing_points_say_where():
    legs = [[(0, 2), (4, 2)], [(2, 0), (2, 4)]]
    anchors = [(0, 2), (4, 2), (2, 0), (2, 4)]
    assert crossing_points(legs, anchors) == [(2, 2)]
    assert crossing_points(legs, [(2, 2), *anchors]) == []


def test_overlap_stretches_say_where_and_how_many_cover_them():
    legs = [[(0, 0), (4, 0)], [(2, 0), (6, 0)], [(3, 0), (5, 0)]]
    stretches = overlap_stretches(legs)
    assert ((2, 0), (3, 0), 2) in stretches and ((3, 0), (4, 0), 3) in stretches
    assert overlap_stretches([[(0, 0), (4, 0)], [(0, 1), (4, 1)]]) == []
    assert overlap_stretches([[(1, 0), (1, 5)], [(1, 3), (1, 8)]]) == [((1, 3), (1, 5), 2)]


@pytest.mark.parametrize(
    "build",
    [designs.slide5_design, designs.slide5_with_crossing, designs.slide5_with_overlap],
)
def test_marker_counts_match_the_scores_exactly(build):
    design = build()
    metrics = compute_metrics(design)
    markers = design_markers(design)
    assert len(markers["crossings"]) == metrics.X
    assert len(markers["bends"]) == metrics.B
    assert (len(markers["overlaps"]) > 0) == (metrics.O > 0)


def test_overlap_marker_lengths_add_up_to_the_overlap_score_when_two_cables_cover(plan):
    design = designs.slide5_with_overlap()
    markers = design_markers(design)
    assert sum(math.dist(a, b) for a, b in markers["overlaps"]) == pytest.approx(compute_metrics(design).O)


# ----------------------------------------------------------- the structure


def test_overlay_has_exactly_the_agreed_fields_and_no_problems(good):
    assert tuple(good) == OVERLAY_KEYS
    assert overlay_schema_problems(good) == []
    assert good["schema_version"] == SCHEMA_VERSION == 1
    assert good["units"] == "m" and good["plan_id"] == "SLIDE5_PLAN"


def test_overlay_is_plain_json(good):
    assert json.loads(json.dumps(good)) == good


def test_every_point_is_a_pair_of_finite_numbers(good):
    points = list(all_points(good))
    assert len(points) > 100
    assert all(math.isfinite(v) for p in points for v in p)


def test_good_plan_status(good):
    assert good["status"] == "ok" and good["valid"] is True
    assert good["violations"] == []
    assert good["bounds"] == {"min": [0.0, 0.0], "max": [14.0, 9.0]}
    assert good["scale"] == 18.0


# --------------------------------------------------------------- the house


def test_house_geometry(good):
    geometry = good["geometry"]
    assert len(geometry["rooms"]) == 7
    assert len(geometry["walls"]) >= 1 and len(geometry["inner"]) == 1
    assert len(geometry["windows"]) == 3
    rooms = {r["room_id"]: r for r in geometry["rooms"]}
    assert rooms["R07"]["eligible"] is False and rooms["R07"]["hub_id"] is None
    assert rooms["R05"]["eligible"] is True and rooms["R05"]["hub_id"] == "H1"
    assert rooms["R03"]["hub_id"] == "H2"
    assert rooms["R05"]["area_m2"] == 40.0
    outline = rooms["R05"]["polygon"]
    assert outline[0] == outline[-1] and len(outline) == 5  # a closed box
    assert rooms["R05"]["polygons"][0][0] == outline  # first polygon's outer ring


def test_openings(good):
    openings = {o["opening_id"]: o for o in good["geometry"]["openings"]}
    assert len(openings) == 9
    assert openings["D1"]["rooms"] == ["R01", "R05"] and openings["D1"]["counts_for_zone"] is True
    assert openings["E1"]["kind"] == "entrance" and openings["E1"]["counts_for_zone"] is False
    assert openings["D1"]["gap"] == [[3.05, 5.0], [3.95, 5.0]]


# ----------------------------------------------- nodes, hubs, cables, score


def test_nodes_know_their_hub_and_visiting_order(good):
    nodes = {n["node_id"]: n for n in good["nodes"]}
    assert len(nodes) == 6
    assert (nodes["N5"]["hub_id"], nodes["N5"]["order"]) == ("H1", 1)
    assert (nodes["N4"]["hub_id"], nodes["N4"]["order"]) == ("H1", 2)
    assert (nodes["N1"]["hub_id"], nodes["N1"]["order"]) == ("H1", 3)
    assert (nodes["N6"]["hub_id"], nodes["N6"]["order"]) == ("H2", 1)
    assert nodes["N1"]["point"] == [2.0, 7.0]


def test_hubs_and_their_numbered_legs(good):
    h1, h2 = good["hubs"]
    assert (h1["hub_id"], h1["color_index"], h2["color_index"]) == ("H1", 0, 1)
    assert h1["sequence"] == ["H1", "N5", "N4", "N1", "H1"] and h1["rooms"] == ["R05", "R01", "R04"]
    assert [leg["order"] for leg in h1["legs"]] == [1, 2, 3, 4]
    assert h1["legs"][-1]["to"] == "H1"  # the last number is the return to the hub
    assert h1["ring_length_m"] == pytest.approx(27.2) and h2["ring_length_m"] == pytest.approx(23.6)
    assert (h1["area_m2"], h2["area_m2"]) == (71.0, 47.0)
    assert h1["point"] == [5.0, 3.5]
    assert h1["legs"][0]["polyline"][0] == h1["point"]


def test_score_and_rule_summary(good):
    metrics = good["metrics"]
    assert (metrics["hub_count"], metrics["X"], metrics["B"]) == (2, 0, 28)
    assert metrics["J"] == pytest.approx(64.8) and metrics["area_gap_m2"] == pytest.approx(24.0)
    assert metrics["per_hub"]["H1"]["bends"] == 12
    assert [c["rule_id"] for c in good["checks"]][:3] == ["schema", "coverage", "capacity"]
    assert len(good["checks"]) == 9 and all(c["passed"] for c in good["checks"])


def test_bounds_report_defaults_to_the_lower_bound(good):
    assert good["bounds_report"] == {"lower_bound_hubs": 2, "proven_minimum": None, "tried": {}}


def test_bounds_report_from_a_solver_status(plan):
    status = HubCountStatus(lower_bound=2, tried={3: "unresolved", 2: "feasible"}, proven_minimum=True)
    overlay = overlay_for(plan, designs.slide5_design(), hub_count_status=status)
    assert overlay["bounds_report"] == {
        "lower_bound_hubs": 2,
        "proven_minimum": True,
        "tried": {"2": "feasible", "3": "unresolved"},
    }


def test_optional_extras_are_carried(plan):
    cfg = SolverConfig(time_budget_s=12)
    overlay = overlay_for(plan, designs.slide5_design(), runtime_s=3.14159, notes=("fast", 7), cfg=cfg)
    assert overlay["runtime_s"] == 3.142 and overlay["notes"] == ["fast", "7"]
    assert overlay["settings_changed"] == {"time_budget_s": 12.0}
    assert overlay["bounds_report"]["lower_bound_hubs"] == 2


# ----------------------------------------------- markers inside the overlay


def test_markers_for_a_clean_plan(good):
    assert good["markers"]["crossings"] == [] and good["markers"]["overlaps"] == []
    assert len(good["markers"]["bends"]) == 28


def test_markers_for_a_costly_plan(plan):
    overlay = overlay_for(plan, designs.slide5_with_overlap())
    assert overlay["status"] == "ok"
    assert len(overlay["markers"]["crossings"]) == overlay["metrics"]["X"] == 2
    stretch_total = sum(math.dist(a, b) for a, b in overlay["markers"]["overlaps"])
    assert stretch_total == pytest.approx(overlay["metrics"]["O"])


# ------------------------------------------------ invalid and missing designs


def test_invalid_design_is_still_drawable_with_red_flags(plan):
    overlay = overlay_for(plan, designs.broken_cable_through_wall())
    assert overlay["status"] == "invalid" and overlay["valid"] is False
    assert overlay_schema_problems(overlay) == []
    kinds = {v["type"] for v in overlay["violations"]}
    assert "WALL_INTERSECTION" in kinds
    wall = next(v for v in overlay["violations"] if v["type"] == "WALL_INTERSECTION")
    assert wall["hub_id"] == "H1" and wall["segment_index"] == 2 and len(wall["point"]) == 2
    assert any(not c["passed"] for c in overlay["checks"])
    assert overlay["hubs"] and overlay["metrics"] is not None


def test_a_violation_without_a_spot_has_a_null_point(plan):
    overlay = overlay_for(plan, designs.broken_coverage_missing())
    coverage = [v for v in overlay["violations"] if v["type"] == "COVERAGE_MISSING"]
    assert coverage and all(v["point"] is None for v in coverage)


def test_no_design_gives_just_the_house_and_the_reason(plan):
    overlay = build_overlay(plan, None, None, notes=["timeout after 60 s"])
    assert overlay["status"] == "no_design_found" and overlay["valid"] is False
    assert overlay["hubs"] == [] and overlay["violations"] == [] and overlay["checks"] == []
    assert overlay["metrics"] is None
    assert overlay["markers"] == {"crossings": [], "overlaps": [], "bends": []}
    assert overlay["notes"] == ["timeout after 60 s"]
    assert len(overlay["geometry"]["rooms"]) == 7
    assert all(n["hub_id"] is None and n["order"] is None for n in overlay["nodes"])
    assert overlay_schema_problems(overlay) == []


def test_status_can_be_forced_but_only_to_a_known_value(plan):
    assert build_overlay(plan, None, None, status="no_design_found")["status"] == "no_design_found"
    with pytest.raises(ValueError, match="status must be"):
        build_overlay(plan, None, None, status="great")


def test_tiny_plan_overlay():
    plan = tiny_plan()
    design = designs.tiny_design()
    overlay = build_overlay(plan, design, check_design(plan, design))
    assert overlay["status"] == "ok" and len(overlay["hubs"]) == 1 and overlay_schema_problems(overlay) == []
    assert overlay["metrics"]["J"] == pytest.approx(13.0)


# ------------------------------------------------- same frame as the official file


def test_overlay_uses_the_same_coordinates_as_the_official_file(plan):
    design = designs.slide5_design()
    overlay = overlay_for(plan, design)
    official = build_official(plan, design)
    for hub, entry in zip(overlay["hubs"], official["hubs"], strict=True):
        assert hub["point"] == [entry["location"]["x"], entry["location"]["y"]]
        for leg, segment in zip(hub["legs"], entry["route_segments"], strict=True):
            assert leg["polyline"] == segment["polyline"] and leg["length_m"] == segment["length_m"]


def test_the_output_transform_moves_everything_together():
    shifted = slide5_plan().model_copy(update={"output_transform": {"dx": 10.0, "dy": 0.0}})
    design = designs.broken_cable_through_wall()
    overlay = build_overlay(shifted, design, check_design(slide5_plan(), design))
    assert overlay["bounds"] == {"min": [10.0, 0.0], "max": [24.0, 9.0]}
    assert overlay["hubs"][0]["point"] == [15.0, 3.5]
    assert overlay["nodes"][0]["point"][0] == 12.0  # N1 was at x = 2
    wall = next(v for v in overlay["violations"] if v["type"] == "WALL_INTERSECTION")
    assert wall["point"][0] >= 10.0


def test_a_flipped_output_frame_keeps_bounds_ordered():
    flipped = slide5_plan().model_copy(update={"output_transform": {"dx": 0.0, "dy": 9.0, "flip_y": True}})
    overlay = build_overlay(flipped, None, None)
    assert overlay["bounds"]["min"] == [0.0, 0.0] and overlay["bounds"]["max"] == [14.0, 9.0]


# ------------------------------------------------------------- writing


def test_overlay_file_is_written_and_matches(plan, tmp_path):
    design = designs.slide5_design()
    validation = check_design(plan, design)
    path = tmp_path / "out" / "SLIDE5_PLAN.overlay.json"
    returned = write_overlay_json(plan, design, validation, path)
    assert returned == path
    assert json.loads(path.read_text(encoding="utf-8")) == build_overlay(plan, design, validation)
    assert [p.name for p in path.parent.iterdir()] == ["SLIDE5_PLAN.overlay.json"]  # no temp files


def test_invalid_and_missing_designs_are_written_too(plan, tmp_path):
    bad = designs.broken_cable_through_wall()
    write_overlay_json(plan, bad, check_design(plan, bad), tmp_path / "bad.json")
    write_overlay_json(plan, None, None, tmp_path / "none.json", notes=["x"])
    assert json.loads((tmp_path / "bad.json").read_text())["status"] == "invalid"
    assert json.loads((tmp_path / "none.json").read_text())["status"] == "no_design_found"


def test_writing_again_replaces_the_old_file(plan, tmp_path):
    path = tmp_path / "a.json"
    write_overlay_json(plan, None, None, path, notes=["first"])
    write_overlay_json(plan, None, None, path, notes=["second"])
    assert json.loads(path.read_text())["notes"] == ["second"]


def test_unwritable_place_gives_an_output_error(plan, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OutputWriteError):
        write_overlay_json(plan, None, None, blocker / "inside.json")


# ------------------------------------------------------- format checking


def test_schema_check_catches_missing_and_extra_fields(good):
    broken = json.loads(json.dumps(good))
    del broken["markers"]
    broken["debug"] = 1
    problems = " | ".join(overlay_schema_problems(broken))
    assert "missing markers" in problems and "unexpected field debug" in problems


def test_schema_check_catches_wrong_values(good):
    broken = json.loads(json.dumps(good))
    broken["schema_version"] = 2
    broken["status"] = "great"
    broken["units"] = "mm"
    broken["valid"] = "yes"
    broken["hubs"][0]["point"] = [1]
    broken["hubs"][0]["legs"][0]["polyline"] = [[1, 2], "x"]
    problems = " | ".join(overlay_schema_problems(broken))
    for fragment in ("schema_version must be 1", "status must be", 'units must be "m"', "valid must be", "hubs[].point", "polyline"):
        assert fragment in problems


def test_schema_check_never_crashes_on_nonsense():
    assert overlay_schema_problems([]) == ["overlay is not an object"]
    assert overlay_schema_problems({"geometry": 5, "bounds": []})  # lots of problems, no exception
