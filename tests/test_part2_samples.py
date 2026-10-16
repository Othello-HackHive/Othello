"""T15: the sample files for the frontend are real, valid and never stale."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan
from make_samples import SAMPLES_DIR, build_samples

from othello.checker import check_design, score_design
from othello.io.failure import FailureReport, read_failure_json
from othello.io.official import (
    build_official,
    design_from_official,
    official_schema_problems,
)
from othello.io.overlay import OVERLAY_KEYS, SCHEMA_VERSION, overlay_schema_problems

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_FILES = {
    "tiny/TINY_PLAN.json",
    "tiny/TINY_PLAN.overlay.json",
    "valid/SLIDE5_PLAN.json",
    "valid/SLIDE5_PLAN.overlay.json",
    "costly/SLIDE5_PLAN.json",
    "costly/SLIDE5_PLAN.overlay.json",
    "invalid/SLIDE5_PLAN.overlay.json",
    "no_design/SLIDE5_PLAN.overlay.json",
    "no_design/SLIDE5_PLAN.failure.json",
}


def load(relative: str):
    return json.loads((SAMPLES_DIR / relative).read_text(encoding="utf-8"))


def committed_files() -> set[str]:
    return {p.relative_to(SAMPLES_DIR).as_posix() for p in SAMPLES_DIR.rglob("*.json")}


# ----------------------------------------------------- never stale


def test_the_committed_samples_are_exactly_what_the_code_produces(tmp_path):
    written = build_samples(tmp_path)
    fresh = {p.relative_to(tmp_path).as_posix() for p in written}
    assert fresh == EXPECTED_FILES
    assert committed_files() == EXPECTED_FILES, "extra or missing sample files: run python tests/make_samples.py"
    for relative in sorted(fresh):
        # line endings do not matter here (Git on Windows may change them on checkout)
        generated = (tmp_path / relative).read_bytes().replace(b"\r\n", b"\n")
        committed = (SAMPLES_DIR / relative).read_bytes().replace(b"\r\n", b"\n")
        assert generated == committed, f"{relative} is out of date: run python tests/make_samples.py"


def test_making_the_samples_twice_gives_the_same_bytes(tmp_path):
    first = build_samples(tmp_path / "a")
    second = build_samples(tmp_path / "b")
    for a, b in zip(first, second, strict=True):
        assert a.read_bytes() == b.read_bytes()


def test_the_readme_mentions_every_folder_and_file():
    text = (SAMPLES_DIR / "README.md").read_text(encoding="utf-8")
    for relative in EXPECTED_FILES:
        folder, name = relative.split("/")
        assert f"`{folder}/`" in text and f"`{name}`" in text


def test_the_format_note_mentions_every_overlay_field():
    text = (ROOT / "docs" / "OVERLAY_FORMAT.md").read_text(encoding="utf-8")
    for key in OVERLAY_KEYS:
        assert f"`{key}`" in text, f"docs/OVERLAY_FORMAT.md does not mention {key}"
    assert f"`schema_version` = {SCHEMA_VERSION}" in text


def test_no_sample_contains_a_path_from_a_personal_computer():
    for relative in EXPECTED_FILES:
        text = (SAMPLES_DIR / relative).read_text(encoding="utf-8")
        assert not re.search(r"[A-Za-z]:\\\\|/Users/|/home/|AppData", text), relative


def test_git_does_not_ignore_any_sample_file():
    """The .gitignore hides generated outputs and 'data' folders; the samples must be the exception."""
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    files = sorted(p for p in SAMPLES_DIR.rglob("*") if p.is_file())
    assert len(files) >= len(EXPECTED_FILES) + 1  # the json files and the README
    for path in files:
        result = subprocess.run(["git", "check-ignore", "-q", str(path)], cwd=ROOT, capture_output=True, check=False)
        if result.returncode == 128:
            pytest.skip("not a git checkout")
        assert result.returncode == 1, f"git would ignore {path.relative_to(ROOT).as_posix()}"


# ------------------------------------------- every file is valid


@pytest.mark.parametrize("relative", sorted(f for f in EXPECTED_FILES if f.endswith(".overlay.json")))
def test_every_overlay_sample_matches_the_overlay_format(relative):
    data = load(relative)
    assert overlay_schema_problems(data) == []
    assert tuple(data) == OVERLAY_KEYS and data["schema_version"] == SCHEMA_VERSION


@pytest.mark.parametrize(
    "relative", sorted(f for f in EXPECTED_FILES if f.endswith(".json") and ".overlay." not in f and ".failure." not in f)
)
def test_every_official_sample_matches_slide_8_and_passes_the_checker(relative):
    data = load(relative)
    plan = tiny_plan() if data["plan_id"] == "TINY_PLAN" else slide5_plan()
    assert official_schema_problems(data) == []
    assert check_design(plan, design_from_official(plan, data)).valid


# --------------------------------------------- what each folder shows


def test_tiny_sample():
    overlay = load("tiny/TINY_PLAN.overlay.json")
    assert overlay["status"] == "ok" and len(overlay["hubs"]) == 1 and len(overlay["nodes"]) == 2
    assert overlay["metrics"]["J"] == pytest.approx(13.0)
    assert overlay["bounds_report"] == {"lower_bound_hubs": 1, "proven_minimum": True, "tried": {"1": "feasible"}}
    assert load("tiny/TINY_PLAN.json")["reported_metrics"] == {"hub_count": 1, "total_length_m": pytest.approx(10.0)}


def test_valid_sample():
    overlay = load("valid/SLIDE5_PLAN.overlay.json")
    assert overlay["status"] == "ok" and overlay["valid"] is True and overlay["violations"] == []
    assert len(overlay["hubs"]) == 2 and len(overlay["nodes"]) == 6 and len(overlay["geometry"]["rooms"]) == 7
    assert overlay["markers"]["crossings"] == [] and overlay["markers"]["overlaps"] == []
    assert overlay["metrics"]["J"] == pytest.approx(64.8)
    assert all(check["passed"] for check in overlay["checks"]) and len(overlay["checks"]) == 9
    assert overlay["bounds_report"]["proven_minimum"] is True
    assert load("valid/SLIDE5_PLAN.json") == build_official(slide5_plan(), designs.slide5_design())


def test_costly_sample_shows_crossings_and_overlaps_but_is_still_ok():
    overlay = load("costly/SLIDE5_PLAN.overlay.json")
    assert overlay["status"] == "ok" and overlay["violations"] == []
    assert len(overlay["markers"]["crossings"]) == overlay["metrics"]["X"] > 0
    assert len(overlay["markers"]["overlaps"]) > 0 and overlay["metrics"]["O"] > 0
    assert overlay["metrics"]["J"] > load("valid/SLIDE5_PLAN.overlay.json")["metrics"]["J"]


def test_invalid_sample_has_red_marks_and_no_official_file():
    overlay = load("invalid/SLIDE5_PLAN.overlay.json")
    assert overlay["status"] == "invalid" and overlay["valid"] is False
    assert {v["type"] for v in overlay["violations"]} >= {"WALL_INTERSECTION"}
    assert all(v["point"] is not None for v in overlay["violations"])
    assert [c["rule_id"] for c in overlay["checks"] if not c["passed"]] == ["free_space", "doors"]
    assert not (SAMPLES_DIR / "invalid" / "SLIDE5_PLAN.json").exists()


def test_no_design_sample_has_only_the_house_and_the_reason():
    overlay = load("no_design/SLIDE5_PLAN.overlay.json")
    assert overlay["status"] == "no_design_found" and overlay["valid"] is False
    assert overlay["hubs"] == [] and overlay["metrics"] is None and overlay["checks"] == []
    assert len(overlay["geometry"]["rooms"]) == 7 and overlay["notes"]
    assert overlay["bounds_report"]["tried"] == {"2": "unresolved", "3": "unresolved"}
    assert overlay["bounds_report"]["proven_minimum"] is False


def test_failure_sample_reads_back_with_a_known_code_and_a_fixed_time():
    report = read_failure_json(SAMPLES_DIR / "no_design" / "SLIDE5_PLAN.failure.json")
    assert isinstance(report, FailureReport)
    assert (report.code, report.stage, report.plan_id) == ("NO_DESIGN_FOUND", "solve", "SLIDE5_PLAN")
    assert report.created_at == "2026-10-16T12:00:00Z" and report.hint and report.message
    assert report.details["notes"] == ["time budget used up at 2 hubs", "3 hubs not finished either"]


def test_overlay_scores_match_an_independent_rescore_of_the_design():
    plan = slide5_plan()
    for folder, build in (("valid", designs.slide5_design), ("costly", designs.slide5_with_overlap)):
        metrics = score_design(plan, build())
        shown = load(f"{folder}/SLIDE5_PLAN.overlay.json")["metrics"]
        assert shown["J"] == pytest.approx(metrics.J, abs=1e-3) and shown["X"] == metrics.X and shown["B"] == metrics.B
