"""T16: the runner (single plan, batch, summary table, command line)."""

import csv
import json
import pickle

import pytest
from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker import check_design
from othello.io.failure import read_failure_json
from othello.io.official import design_from_official, official_schema_problems
from othello.io.overlay import overlay_schema_problems
from othello.models.config import SolverConfig
from othello.reader.load_plan import InputError
from othello.run import (
    PROGRESS_STAGES,
    SUMMARY_COLUMNS,
    find_plan_files,
    main,
    run_batch,
    run_plan,
    safe_checker,
    summary_text,
)
from othello.solver.types import HubCountSummary, OuterSearchResult

FAST = SolverConfig(time_budget_s=20)


def fake_search(design=None, provisional=None, verified=True, status="verified"):
    """A stand-in for Part 3's search that hands back whatever the test wants."""

    def optimizer(plan, batches, config, checker, cache, deadline, lower_bound):
        return OuterSearchResult(
            status,
            design,
            provisional,
            verified,
            10.0,
            HubCountSummary(lower_bound, {lower_bound: "feasible"}, True),
            1,
            1,
            ("fake search",),
        )

    return optimizer


def fake_partitions(plan, count, config, deadline):
    return [], "unresolved"


def run_tiny(tmp_path, **kwargs):
    return run_plan(tiny_plan(), tmp_path, FAST, partitioner=fake_partitions, **kwargs)


# --------------------------------------------------- the happy path


def test_real_search_on_the_tiny_plan_writes_every_file(tmp_path):
    outcome = run_plan(tiny_plan(), tmp_path, FAST)
    assert outcome.status == "ok" and outcome.hubs == 1 and outcome.lower_bound == 1
    assert outcome.proven_min is True and outcome.failure is None
    for kind in ("official", "overlay", "picture", "log", "settings"):
        assert outcome.files[kind].is_file(), kind
        assert outcome.files[kind].parent == tmp_path
    assert not (tmp_path / "TINY_PLAN.failure.json").exists()
    official = json.loads(outcome.files["official"].read_text())
    assert official_schema_problems(official) == []
    plan = tiny_plan()
    assert check_design(plan, design_from_official(plan, official), FAST).valid
    assert (
        overlay_schema_problems(json.loads(outcome.files["overlay"].read_text())) == []
    )
    assert outcome.metrics["J"] == pytest.approx(
        check_design(plan, designs.tiny_design(), FAST).metrics.J, rel=0.5
    )


def test_files_are_named_after_the_plan_id_not_the_file_name(tmp_path):
    source = tmp_path / "some_file.pkl"
    source.write_bytes(b"x")
    out = tmp_path / "out"
    outcome = run_plan(
        source,
        out,
        FAST,
        loader=lambda path, cfg: tiny_plan(),
        partitioner=fake_partitions,
        optimizer=fake_search(designs.tiny_design()),
        picture=False,
    )
    assert outcome.status == "ok" and outcome.plan_id == "TINY_PLAN"
    assert (out / "TINY_PLAN.json").is_file() and (
        out / "TINY_PLAN.overlay.json"
    ).is_file()
    assert not (out / "some_file.json").exists()


def test_a_timeout_that_still_holds_a_verified_design_is_a_success(tmp_path):
    outcome = run_tiny(
        tmp_path,
        optimizer=fake_search(designs.tiny_design(), status="timeout"),
        picture=False,
    )
    assert outcome.status == "ok" and outcome.files["official"].is_file()
    assert any("time ran out" in note for note in outcome.notes)


def test_the_picture_can_be_skipped(tmp_path):
    outcome = run_tiny(
        tmp_path, optimizer=fake_search(designs.tiny_design()), picture=False
    )
    assert "picture" not in outcome.files and not (tmp_path / "TINY_PLAN.png").exists()


# --------------------------------------------------- never submit the wrong thing


def test_a_provisional_design_is_never_submitted(tmp_path):
    optimizer = fake_search(
        None, provisional=designs.tiny_design(), verified=False, status="unverified"
    )
    outcome = run_tiny(tmp_path, optimizer=optimizer)
    assert (
        outcome.status == "no_design_found"
        and outcome.failure.code == "NO_DESIGN_FOUND"
    )
    assert not (tmp_path / "TINY_PLAN.json").exists()
    assert read_failure_json(tmp_path / "TINY_PLAN.failure.json").stage == "solve"


def test_a_design_the_final_check_rejects_is_not_written(tmp_path):
    optimizer = fake_search(designs.broken_hub_in_wall())
    outcome = run_tiny(tmp_path, optimizer=optimizer)
    assert outcome.status == "no_design_found"
    assert "final check" in outcome.failure.message
    assert not (tmp_path / "TINY_PLAN.json").exists()


def test_a_design_marked_unverified_is_not_written_even_if_it_is_valid(tmp_path):
    outcome = run_tiny(
        tmp_path, optimizer=fake_search(designs.tiny_design(), verified=False)
    )
    assert (
        outcome.status == "no_design_found"
        and not (tmp_path / "TINY_PLAN.json").exists()
    )


def test_old_results_are_removed_when_a_new_run_fails(tmp_path):
    good = run_tiny(
        tmp_path, optimizer=fake_search(designs.tiny_design()), picture=True
    )
    assert good.status == "ok" and (tmp_path / "TINY_PLAN.json").exists()
    bad = run_tiny(
        tmp_path, optimizer=fake_search(None, status="no_design_found", verified=False)
    )
    assert bad.status == "no_design_found"
    assert (
        not (tmp_path / "TINY_PLAN.json").exists()
        and not (tmp_path / "TINY_PLAN.png").exists()
    )
    assert (tmp_path / "TINY_PLAN.failure.json").exists()
    overlay = json.loads((tmp_path / "TINY_PLAN.overlay.json").read_text())
    assert overlay["status"] == "no_design_found"


def test_a_good_run_removes_an_old_failure_note(tmp_path):
    run_tiny(
        tmp_path, optimizer=fake_search(None, verified=False, status="no_design_found")
    )
    assert (tmp_path / "TINY_PLAN.failure.json").exists()
    run_tiny(tmp_path, optimizer=fake_search(designs.tiny_design()), picture=False)
    assert not (tmp_path / "TINY_PLAN.failure.json").exists()


# --------------------------------------------------- the failure paths


def test_a_checker_crash_counts_as_invalid():
    result = safe_checker(
        None, designs.tiny_design(), FAST
    )  # plan=None makes the checker crash
    assert result.valid is False and result.violations[0].type == "OUTPUT_SCHEMA"
    assert "checker crashed" in result.violations[0].detail


def test_an_unreadable_plan_gives_a_failure_note_named_after_the_file(tmp_path):
    source = tmp_path / "broken.pkl"
    source.write_bytes(pickle.dumps(["not", "a", "record"]))
    outcome = run_plan(source, tmp_path / "out", FAST)
    assert (
        outcome.status == "failed"
        and outcome.failure.code == "BAD_INPUT"
        and outcome.failure.stage == "load"
    )
    note = read_failure_json(tmp_path / "out" / "broken.failure.json")
    assert note.plan_id == "broken"
    assert not list(
        (tmp_path / "out").glob("*.overlay.json")
    )  # no plan, nothing to draw


def test_a_missing_file_is_a_failure_note_not_a_crash(tmp_path):
    outcome = run_plan(tmp_path / "ghost.pkl", tmp_path / "out", FAST)
    assert outcome.failure.code == "INPUT_NOT_FOUND"


def test_a_room_that_is_too_big_stops_early(tmp_path):
    cfg = SolverConfig(max_area_per_hub_m2=5.0, time_budget_s=20)
    outcome = run_plan(
        tiny_plan(), tmp_path, cfg, optimizer=fake_search(designs.tiny_design())
    )
    assert (
        outcome.status == "no_design_found"
        and "over the 5 m2 limit" in outcome.failure.message
    )
    assert outcome.failure.settings_changed == {
        "max_area_per_hub_m2": 5.0,
        "time_budget_s": 20.0,
    }


def test_no_time_left_for_setup_is_a_clear_failure(tmp_path):
    cfg = SolverConfig(time_budget_s=1e-9)
    outcome = run_plan(
        tiny_plan(), tmp_path, cfg, optimizer=fake_search(designs.tiny_design())
    )
    assert (
        outcome.status == "no_design_found" and "time budget" in outcome.failure.message
    )


def test_an_unexpected_crash_in_the_search_becomes_an_internal_error(tmp_path):
    def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    outcome = run_tiny(tmp_path, optimizer=exploding)
    assert outcome.status == "failed" and outcome.failure.code == "INTERNAL_ERROR"
    assert outcome.failure.stage == "solve" and "boom" in outcome.failure.message


def test_a_failure_to_write_the_official_file_is_reported(tmp_path):
    (tmp_path / "TINY_PLAN.json").mkdir()  # a folder is in the way of the file
    outcome = run_tiny(
        tmp_path, optimizer=fake_search(designs.tiny_design()), picture=False
    )
    assert outcome.status == "failed" and outcome.failure.code == "OUTPUT_WRITE_FAILED"


def test_the_log_file_records_the_run(tmp_path):
    outcome = run_tiny(
        tmp_path, optimizer=fake_search(designs.tiny_design()), picture=False
    )
    text = outcome.files["log"].read_text()
    assert "loaded plan TINY_PLAN" in text and "verified design" in text


# --------------------------------------------------- batch and summary


def make_plan_files(folder, names):
    folder.mkdir(parents=True, exist_ok=True)
    for name in names:
        (folder / name).write_bytes(b"x")
    return folder


def test_find_plan_files_sorts_and_filters(tmp_path):
    folder = make_plan_files(
        tmp_path / "plans", ["b.pkl", "a.pkl", "notes.txt", "c.PICKLE"]
    )
    assert [p.name for p in find_plan_files(folder)] == ["a.pkl", "b.pkl", "c.PICKLE"]
    assert find_plan_files(folder / "a.pkl") == [folder / "a.pkl"]
    with pytest.raises(FileNotFoundError):
        find_plan_files(tmp_path / "nothing")


def test_a_bad_plan_does_not_stop_the_batch_and_the_summary_lists_all(tmp_path):
    folder = make_plan_files(tmp_path / "plans", ["a.pkl", "b.pkl", "c.pkl"])

    def loader(path, cfg):
        if path.stem == "b":
            raise InputError("garbled")
        return tiny_plan()

    summary = tmp_path / "out" / "summary.csv"
    outcomes = run_batch(
        folder,
        tmp_path / "out",
        FAST,
        summary,
        loader=loader,
        partitioner=fake_partitions,
        optimizer=fake_search(designs.tiny_design()),
        picture=False,
    )
    assert [o.status for o in outcomes] == ["ok", "failed", "ok"]
    rows = list(csv.DictReader(summary.open(newline="")))
    assert tuple(rows[0]) == SUMMARY_COLUMNS and [r["plan_id"] for r in rows] == [
        "TINY_PLAN",
        "b",
        "TINY_PLAN",
    ]
    assert (
        rows[1]["status"] == "failed"
        and "BAD_INPUT" in rows[1]["notes"]
        and rows[1]["J"] == ""
    )
    assert (
        rows[0]["hubs"] == "1"
        and rows[0]["proven_min"] == "true"
        and float(rows[0]["J"]) > 0
    )


def test_the_summary_text_has_a_header_even_with_no_plans():
    assert summary_text([]).strip().split(",") == list(SUMMARY_COLUMNS)


# --------------------------------------------------- the command line


def test_bad_settings_stop_the_command_with_code_2(tmp_path, capsys):
    code = main([str(tmp_path), "--out", str(tmp_path), "--time-budget", "-5"], env={})
    assert code == 2 and "error:" in capsys.readouterr().err


def test_a_missing_source_stops_the_command_with_code_2(tmp_path, capsys):
    assert main([str(tmp_path / "ghost"), "--out", str(tmp_path)], env={}) == 2
    assert "no such plan" in capsys.readouterr().err


def test_an_unreadable_plan_still_exits_with_0_and_writes_the_note(tmp_path, capsys):
    source = tmp_path / "broken.pkl"
    source.write_bytes(b"not a pickle")
    out = tmp_path / "out"
    code = main(
        [str(source), "--out", str(out), "--summary", str(out / "s.csv")], env={}
    )
    assert code == 0
    assert (out / "broken.failure.json").exists() and (out / "s.csv").exists()
    assert "broken: failed" in capsys.readouterr().out


def test_environment_settings_reach_the_run(tmp_path):
    source = tmp_path / "broken.pkl"
    source.write_bytes(b"x")
    main(
        [str(source), "--out", str(tmp_path / "out"), "--quiet"],
        env={"OTHELLO_TIME_BUDGET_S": "7"},
    )
    settings = (
        json.loads((tmp_path / "out" / "broken.settings.json").read_text())
        if (tmp_path / "out" / "broken.settings.json").exists()
        else None
    )
    # the plan never loaded, so no settings file; the failure note still lists the changed setting
    assert settings is None
    note = read_failure_json(tmp_path / "out" / "broken.failure.json")
    assert note.settings_changed == {"time_budget_s": 7.0}


def test_real_search_on_the_seven_room_house_never_submits_anything_unchecked(tmp_path):
    # Part 3 is still slow on this house, so whether it finishes inside the budget depends on the
    # machine.  Either way the promises must hold: a checked answer, or a clear failure note.
    plan = slide5_plan()
    outcome = run_plan(plan, tmp_path, SolverConfig(time_budget_s=20), picture=False)
    if outcome.status == "ok":
        official = json.loads(outcome.files["official"].read_text())
        assert check_design(plan, design_from_official(plan, official), FAST).valid
    else:
        assert (
            outcome.failure.code == "NO_DESIGN_FOUND"
            and not (tmp_path / "SLIDE5_PLAN.json").exists()
        )


# --------------------------------------------------- progress steps (for the live view)


def collect_events(tmp_path, **kwargs):
    events = []
    outcome = run_tiny(tmp_path, progress=events.append, picture=False, **kwargs)
    return outcome, events


def test_a_successful_run_reports_its_steps_in_order(tmp_path):
    outcome, events = collect_events(
        tmp_path, optimizer=fake_search(designs.tiny_design())
    )
    stages = [e["stage"] for e in events]
    assert outcome.status == "ok"
    assert stages == [
        "started",
        "loaded",
        "bound",
        "searching",
        "checking",
        "writing",
        "done",
    ]
    assert stages == [s for s in PROGRESS_STAGES if s in stages]
    done = events[-1]
    assert (
        done["status"] == "ok"
        and done["hubs"] == 1
        and done["J"] > 0
        and done["code"] is None
    )
    assert (
        events[1]["rooms"] == 2
        and events[2]["lower_bound"] == 1
        and events[3]["budget_s"] == 20
    )


def test_every_event_is_plain_data_with_a_message_and_a_rising_clock(tmp_path):
    _, events = collect_events(tmp_path, optimizer=fake_search(designs.tiny_design()))
    assert json.loads(json.dumps(events)) == events
    assert all(e["message"] and isinstance(e["elapsed_s"], float) for e in events)
    assert [e["elapsed_s"] for e in events] == sorted(e["elapsed_s"] for e in events)


def test_a_failed_run_still_ends_with_a_done_step_that_says_why(tmp_path):
    outcome, events = collect_events(
        tmp_path, optimizer=fake_search(None, verified=False, status="no_design_found")
    )
    assert outcome.status == "no_design_found"
    assert events[-1]["stage"] == "done" and events[-1]["status"] == "no_design_found"
    assert (
        events[-1]["code"] == "NO_DESIGN_FOUND" and "no design" in events[-1]["message"]
    )


def test_an_unreadable_plan_reports_start_and_done_only(tmp_path):
    events = []
    source = tmp_path / "broken.pkl"
    source.write_bytes(b"x")
    run_plan(source, tmp_path / "out", FAST, progress=events.append)
    assert [e["stage"] for e in events] == ["started", "done"] and events[-1][
        "status"
    ] == "failed"


def test_a_broken_progress_listener_never_stops_the_run(tmp_path):
    def broken(event):
        raise RuntimeError("listener crashed")

    outcome = run_tiny(
        tmp_path,
        progress=broken,
        optimizer=fake_search(designs.tiny_design()),
        picture=False,
    )
    assert outcome.status == "ok" and "progress listener failed" in outcome.files[
        "log"
    ].read_text(encoding="utf-8")
