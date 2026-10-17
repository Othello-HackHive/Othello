"""T11: errors, failure.json, safe file names and the run diary."""

import json
import logging

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan

from othello.io.errors import (
    FAILURE_CODES,
    STAGES,
    NoDesignFoundError,
    OthelloError,
    OutputWriteError,
)
from othello.io.failure import (
    FailureReport,
    failure_from_exception,
    read_failure_json,
    write_failure_json,
)
from othello.io.official import UnverifiedDesignError, write_official_json
from othello.io.paths import FALLBACK_NAME, output_paths, safe_name
from othello.io.runlog import log_failure, log_settings, run_logger
from othello.io.settings import ConfigError, load_config
from othello.reader.load_plan import InputError

FIXED_TIME = "2026-10-16T12:00:00Z"
REPORT_KEYS = {
    "schema_version",
    "status",
    "plan_id",
    "code",
    "stage",
    "message",
    "hint",
    "details",
    "settings_changed",
    "created_at",
}


def report_for(exc, **kwargs):
    return failure_from_exception(exc, created_at=FIXED_TIME, **kwargs)


# ------------------------------------------------------- the error family


def test_our_errors_share_one_family():
    assert issubclass(ConfigError, OthelloError) and issubclass(ConfigError, ValueError)
    assert issubclass(UnverifiedDesignError, OthelloError)
    assert issubclass(NoDesignFoundError, OthelloError)
    assert issubclass(OutputWriteError, OthelloError)


def test_every_error_class_has_a_known_code_and_stage():
    for cls in (ConfigError, UnverifiedDesignError, NoDesignFoundError, OutputWriteError, OthelloError):
        assert cls.code in FAILURE_CODES
        assert cls.stage in STAGES


def test_existing_behaviour_is_unchanged():
    with pytest.raises(ValueError):
        load_config(overrides=["time_budget_s=0"])
    plan = slide5_plan()
    with pytest.raises(UnverifiedDesignError) as caught:
        write_official_json(plan, designs.broken_cable_through_wall(), "unused.json")
    assert caught.value.violations


def test_no_design_found_carries_its_notes():
    error = NoDesignFoundError(notes=["timeout at 2 hubs"])
    assert error.notes == ["timeout at 2 hubs"]
    assert error.details == {"notes": ["timeout at 2 hubs"]}


# --------------------------------------------------- turning errors into notes


def test_settings_error_note():
    report = report_for(ConfigError("unknown setting 'x'"), plan_id="P1")
    assert (report.code, report.stage) == ("BAD_SETTINGS", "settings")
    assert report.message == "unknown setting 'x'" and report.hint
    assert report.plan_id == "P1" and report.created_at == FIXED_TIME


def test_no_design_note_has_the_solver_notes():
    report = report_for(NoDesignFoundError("nothing valid", notes=["timeout"]))
    assert (report.code, report.stage) == ("NO_DESIGN_FOUND", "solve")
    assert report.details["notes"] == ["timeout"]


def test_invalid_design_note_lists_the_broken_rules():
    plan = slide5_plan()
    with pytest.raises(UnverifiedDesignError) as caught:
        write_official_json(plan, designs.broken_cable_through_wall(), "unused.json")
    report = report_for(caught.value, plan_id="SLIDE5_PLAN")
    assert (report.code, report.stage) == ("DESIGN_INVALID", "check")
    assert "WALL_INTERSECTION" in report.details["violation_codes"]
    assert report.details["violation_count"] == len(report.details["violations"])
    first = report.details["violations"][0]
    assert {"type", "hub_id", "detail"} <= set(first)


def test_long_violation_lists_are_cut_but_still_counted():
    plan = slide5_plan()
    with pytest.raises(UnverifiedDesignError) as caught:
        write_official_json(plan, designs.broken_capacity_all_in_one_hub(), "unused.json")
    error = caught.value
    error.violations = error.violations * 40
    report = report_for(error)
    assert report.details["violation_count"] == len(error.violations)
    assert len(report.details["violations"]) == 50


def test_unreadable_plan_notes():
    assert report_for(InputError("bad geometry")).code == "BAD_INPUT"
    assert report_for(InputError("x")).stage == "load"
    assert report_for(FileNotFoundError("plan.json")).code == "INPUT_NOT_FOUND"
    assert report_for(json.JSONDecodeError("bad", "{", 1)).code == "BAD_INPUT"
    assert report_for(UnicodeDecodeError("utf-8", b"\xff", 0, 1, "bad")).code == "BAD_INPUT"


def test_other_file_problems_and_output_errors():
    assert report_for(PermissionError("denied")).code == "IO_ERROR"
    assert report_for(OutputWriteError("disk full")).code == "OUTPUT_WRITE_FAILED"


def test_unknown_exception_becomes_an_internal_error_with_a_traceback():
    try:
        raise RuntimeError("boom")
    except RuntimeError as problem:
        report = report_for(problem)
    assert report.code == "INTERNAL_ERROR"
    assert report.details["exception"] == "RuntimeError"
    assert "boom" in report.details["traceback"] and "RuntimeError" in report.details["traceback"]


def test_an_empty_message_still_says_something():
    assert report_for(RuntimeError()).message == "RuntimeError"


def test_stage_and_details_can_be_overridden():
    report = report_for(RuntimeError("x"), stage="write", details={"hub_count": 2}, settings_changed={"time_budget_s": 5})
    assert report.stage == "write"
    assert report.details["hub_count"] == 2
    assert report.settings_changed == {"time_budget_s": 5}


def test_every_note_uses_a_known_code():
    errors = [ConfigError("x"), NoDesignFoundError(), OutputWriteError("x"), InputError("x"), FileNotFoundError(), RuntimeError()]
    assert all(report_for(e).code in FAILURE_CODES for e in errors)


# ------------------------------------------------------------ the file


def test_failure_file_has_exactly_the_agreed_fields(tmp_path):
    path = write_failure_json(report_for(ConfigError("bad"), plan_id="P1"), tmp_path / "out" / "P1.failure.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert set(data) == REPORT_KEYS
    assert data["status"] == "failed" and data["schema_version"] == 1
    assert data["created_at"] == FIXED_TIME


def test_failure_file_reads_back_the_same(tmp_path):
    report = report_for(NoDesignFoundError(notes=["a", "b"]), plan_id="P2", settings_changed={"weights.bend": 2.0})
    path = write_failure_json(report, tmp_path / "f.json")
    assert read_failure_json(path) == report


def test_failure_file_is_written_cleanly_with_no_temp_files(tmp_path):
    write_failure_json(report_for(RuntimeError("x")), tmp_path / "f.json")
    assert [p.name for p in tmp_path.iterdir()] == ["f.json"]


def test_failure_file_replaces_an_old_one(tmp_path):
    path = tmp_path / "f.json"
    write_failure_json(report_for(ConfigError("first")), path)
    write_failure_json(report_for(ConfigError("second")), path)
    assert read_failure_json(path).message == "second"


def test_unwritable_place_raises_an_output_error(tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("x")
    with pytest.raises(OutputWriteError):
        write_failure_json(report_for(RuntimeError("x")), blocker / "inside.json")


def test_report_schema_rejects_a_different_status():
    with pytest.raises(ValueError):
        FailureReport(status="ok", code="X", stage="load", message="m")


# -------------------------------------------------------------- file names


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("PLAN_0123", "PLAN_0123"),
        ("plan-1.v2", "plan-1.v2"),
        ("PLAN 1/2", "PLAN_1_2"),
        ("../../etc/passwd", "etc_passwd"),
        ("..", FALLBACK_NAME),
        ("", FALLBACK_NAME),
        (None, FALLBACK_NAME),
        ("////", FALLBACK_NAME),
        ("..\\..\\windows\\system32", "windows_system32"),
        ("plan:*?<>|", "plan"),
        ("réseau", "r_seau"),
    ],
)
def test_safe_name(raw, expected):
    assert safe_name(raw) == expected


def test_safe_name_is_never_longer_than_the_limit():
    assert len(safe_name("a" * 500)) == 100


def test_safe_name_never_contains_a_separator():
    for raw in ("a/b", "a\\b", "../x", "/abs", "C:\\temp"):
        name = safe_name(raw)
        assert "/" not in name and "\\" not in name and name not in {"", ".", ".."}


def test_output_paths_all_live_inside_the_output_folder(tmp_path):
    paths = output_paths(tmp_path, "../../evil")
    assert set(paths) == {"official", "baseline", "overlay", "picture", "log", "failure", "settings"}
    for path in paths.values():
        assert path.resolve().parent == tmp_path.resolve()
    assert paths["official"].name == "evil.json"
    assert paths["baseline"].name == "evil.baseline.json"
    assert paths["overlay"].name == "evil.overlay.json"
    assert paths["failure"].name == "evil.failure.json"
    assert paths["log"].name == "evil.log.txt"
    assert paths["picture"].name == "evil.png"


# ------------------------------------------------------------------- the diary


def test_diary_is_written_to_the_file(tmp_path):
    path = tmp_path / "logs" / "P1.log.txt"
    with run_logger("P1", path) as log:
        log.info("loaded plan")
        log.warning("something odd")
    text = path.read_text(encoding="utf-8")
    assert "INFO" in text and "loaded plan" in text and "WARNING" in text and "something odd" in text


def test_diary_closes_its_handlers_afterwards(tmp_path):
    with run_logger("P1", tmp_path / "a.txt") as log:
        assert log.handlers
    assert log.handlers == []


def test_two_plans_do_not_mix_their_diaries(tmp_path):
    with run_logger("A", tmp_path / "a.txt") as a, run_logger("B", tmp_path / "b.txt") as b:
        a.info("only for A")
        b.info("only for B")
    assert "only for A" in (tmp_path / "a.txt").read_text() and "only for B" not in (tmp_path / "a.txt").read_text()
    assert "only for B" in (tmp_path / "b.txt").read_text() and "only for A" not in (tmp_path / "b.txt").read_text()


def test_running_the_same_plan_twice_does_not_duplicate_lines(tmp_path):
    path = tmp_path / "a.txt"
    with run_logger("P", path) as log:
        log.info("first run")
    with run_logger("P", path) as log:
        log.info("second run")
    text = path.read_text()
    assert text.count("second run") == 1 and "first run" not in text  # a new run starts a fresh diary


def test_diary_does_not_leak_into_the_global_log(tmp_path, caplog):
    with caplog.at_level(logging.INFO), run_logger("P", tmp_path / "a.txt") as log:
        log.info("private")
    assert "private" not in caplog.text


def test_diary_can_also_print_to_the_console(tmp_path, capsys):
    with run_logger("P", None, console=True) as log:
        log.info("shown on screen")
    assert "shown on screen" in capsys.readouterr().err


def test_diary_lists_changed_settings_and_failures(tmp_path):
    path = tmp_path / "a.txt"
    cfg = load_config(overrides=["weights.crossing=9"])
    report = report_for(NoDesignFoundError("nothing valid"), plan_id="P")
    with run_logger("P", path) as log:
        log_settings(log, cfg)
        log_failure(log, report)
    text = path.read_text()
    assert "weights.crossing" in text and "9.0" in text
    assert "FAILED [NO_DESIGN_FOUND/solve] nothing valid" in text and "hint:" in text


def test_diary_says_none_when_settings_are_the_defaults(tmp_path):
    path = tmp_path / "a.txt"
    with run_logger("P", path) as log:
        log_settings(log, load_config())
    assert "settings changed from defaults: none" in path.read_text()
