"""T17: every failure path of APP_FLOW section 4, end to end through the runner.

One table of broken situations; for each, the same promises must hold:
a clear failure note, no official answer, no picture, no half-written files.
"""

import json
import pickle

import pytest
from fixtures import designs
from fixtures.plans import tiny_plan

from othello.io.errors import FAILURE_CODES, STAGES, OutputWriteError
from othello.io.failure import read_failure_json
from othello.io.picture import MissingDependencyError
from othello.models.config import SolverConfig
from othello.run import main, run_batch, run_plan
from othello.solver.types import HubCountSummary, OuterSearchResult

CFG = SolverConfig(time_budget_s=20)


def search(design=None, provisional=None, verified=False, status="no_design_found"):
    def optimizer(plan, batches, config, checker, cache, deadline, lower_bound):
        summary = HubCountSummary(lower_bound, {lower_bound: "unresolved"}, False)
        return OuterSearchResult(
            status, design, provisional, verified, None, summary, 1, 1, ()
        )

    return optimizer


def no_partitions(plan, count, config, deadline):
    return [], "unresolved"


def crash(*args, **kwargs):
    raise RuntimeError("boom")


def run_tiny(out, **kwargs):
    kwargs.setdefault("partitioner", no_partitions)
    kwargs.setdefault("picture", False)
    return run_plan(tiny_plan(), out, kwargs.pop("cfg", CFG), **kwargs)


# Each case: name -> (how to run it, expected status, expected code, expected stage)
def case_unreadable_plan(out):
    source = out.parent / "bad.pkl"
    source.write_bytes(pickle.dumps(["not", "a", "plan"]))
    return run_plan(source, out, CFG), "failed", "BAD_INPUT", "load"


def case_missing_plan(out):
    return (
        run_plan(out.parent / "ghost.pkl", out, CFG),
        "failed",
        "INPUT_NOT_FOUND",
        "load",
    )


def case_room_over_the_limit(out):
    cfg = SolverConfig(max_area_per_hub_m2=5.0, time_budget_s=20)
    return (
        run_tiny(out, cfg=cfg, optimizer=search()),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_no_time_for_setup(out):
    cfg = SolverConfig(time_budget_s=1e-9)
    return (
        run_tiny(out, cfg=cfg, optimizer=search()),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_search_finds_nothing(out):
    return (
        run_tiny(out, optimizer=search()),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_time_runs_out_with_no_design(out):
    return (
        run_tiny(out, optimizer=search(status="timeout")),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_only_a_provisional_design(out):
    optimizer = search(provisional=designs.tiny_design(), status="unverified")
    return (
        run_tiny(out, optimizer=optimizer),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_design_fails_the_final_check(out):
    optimizer = search(
        designs.broken_cable_through_wall(), verified=True, status="verified"
    )
    return (
        run_tiny(out, optimizer=optimizer),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_checker_crashes(out):
    optimizer = search(designs.tiny_design(), verified=True, status="verified")
    return (
        run_tiny(out, optimizer=optimizer, checker=crash),
        "failed",
        "INTERNAL_ERROR",
        "solve",
    )


def case_search_crashes(out):
    return run_tiny(out, optimizer=crash), "failed", "INTERNAL_ERROR", "solve"


def case_every_hub_count_proven_impossible(out):
    # The real Part 3 search, told by Part 1 that no grouping can ever work.
    def impossible(plan, count, config, deadline):
        return [], "proven_infeasible"

    return (
        run_tiny(out, partitioner=impossible),
        "no_design_found",
        "NO_DESIGN_FOUND",
        "solve",
    )


def case_official_file_cannot_be_written(out):
    (out / "TINY_PLAN.json").mkdir(parents=True)
    optimizer = search(designs.tiny_design(), verified=True, status="verified")
    return run_tiny(out, optimizer=optimizer), "failed", "OUTPUT_WRITE_FAILED", "write"


CASES = [
    case_unreadable_plan,
    case_missing_plan,
    case_room_over_the_limit,
    case_no_time_for_setup,
    case_search_finds_nothing,
    case_time_runs_out_with_no_design,
    case_only_a_provisional_design,
    case_design_fails_the_final_check,
    case_checker_crashes,
    case_search_crashes,
    case_every_hub_count_proven_impossible,
    case_official_file_cannot_be_written,
]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.__name__.removeprefix("case_"))
def test_every_failure_keeps_the_same_promises(case, tmp_path):
    out = tmp_path / "out"
    outcome, status, code, stage = case(out)

    assert outcome.status == status
    assert (
        outcome.failure is not None
        and outcome.failure.code == code
        and outcome.failure.stage == stage
    )
    assert code in FAILURE_CODES and stage in STAGES

    # A clear note, written to disk, that says the same thing as the outcome.
    note = read_failure_json(outcome.files["failure"])
    assert note.status == "failed" and note.code == code and note.stage == stage
    assert note.plan_id == outcome.plan_id and note.message and note.hint

    # Never an official answer or a picture of a design that does not exist.
    assert not list(out.glob("*.png"))
    official = [
        p
        for p in out.iterdir()
        if p.is_file() and p.suffix == ".json" and p.name.count(".") == 1
    ]
    assert official == []
    assert "official" not in outcome.files and "picture" not in outcome.files

    # No half-written files left behind, and the diary says what happened.
    assert not list(out.glob("*.tmp")) and not list(out.glob(".*.tmp"))
    assert "FAILED" in outcome.files["log"].read_text(encoding="utf-8")

    # The summary table can say so too.
    row = outcome.summary_row()
    assert row["status"] == status and code in row["notes"] and row["J"] == ""


def test_a_failed_run_with_a_loaded_plan_still_gives_the_frontend_something_to_draw(
    tmp_path,
):
    outcome, *_ = case_search_finds_nothing(tmp_path / "out")
    overlay = json.loads(outcome.files["overlay"].read_text(encoding="utf-8"))
    assert overlay["status"] == "no_design_found" and overlay["hubs"] == []


def test_a_failed_run_with_no_plan_writes_no_overlay(tmp_path):
    outcome, *_ = case_unreadable_plan(tmp_path / "out")
    assert "overlay" not in outcome.files


# ----------------------------------------------- problems with the writing itself


def test_a_missing_picture_library_only_skips_the_picture(tmp_path, monkeypatch):
    def no_matplotlib(*args, **kwargs):
        raise MissingDependencyError("matplotlib is not installed")

    monkeypatch.setattr("othello.run.write_plan_picture", no_matplotlib)
    optimizer = search(designs.tiny_design(), verified=True, status="verified")
    outcome = run_tiny(tmp_path, optimizer=optimizer, picture=True)
    assert outcome.status == "ok" and "picture" not in outcome.files
    assert "picture skipped" in outcome.notes and outcome.files["official"].is_file()


def test_a_blocked_failure_note_does_not_crash_the_run(tmp_path):
    (tmp_path / "TINY_PLAN.failure.json").mkdir()
    outcome = run_tiny(tmp_path, optimizer=search())
    assert outcome.status == "no_design_found" and "failure" not in outcome.files
    assert "could not write the failure note" in outcome.files["log"].read_text(
        encoding="utf-8"
    )


def test_a_blocked_overlay_does_not_hide_the_failure_note(tmp_path):
    (tmp_path / "TINY_PLAN.overlay.json").mkdir()
    outcome = run_tiny(tmp_path, optimizer=search())
    assert outcome.status == "no_design_found" and outcome.files["failure"].is_file()


def test_an_output_folder_that_is_a_file_is_a_clear_error(tmp_path, capsys):
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    with pytest.raises(OutputWriteError, match="cannot use the output folder"):
        run_tiny(blocker, optimizer=search())
    source = tmp_path / "p.pkl"
    source.write_bytes(b"x")
    assert main([str(source), "--out", str(blocker)], env={}) == 1
    assert "cannot use the output folder" in capsys.readouterr().err


def test_a_good_plan_after_a_bad_one_in_the_same_folder_is_unaffected(tmp_path):
    out = tmp_path / "out"
    bad = run_tiny(out, optimizer=search())
    good = run_tiny(
        out, optimizer=search(designs.tiny_design(), verified=True, status="verified")
    )
    assert bad.status == "no_design_found" and good.status == "ok"
    assert (
        good.files["official"].is_file()
        and not (out / "TINY_PLAN.failure.json").exists()
    )


def test_a_batch_with_every_kind_of_trouble_still_finishes_and_lists_everything(
    tmp_path,
):
    folder = tmp_path / "plans"
    folder.mkdir()
    for name in ("a", "b", "c", "d"):
        (folder / f"{name}.pkl").write_bytes(b"x")

    def loader(path, cfg):
        if path.stem == "a":
            raise ValueError("garbled")
        return tiny_plan()

    outcomes = run_batch(
        folder,
        tmp_path / "out",
        CFG,
        tmp_path / "out" / "s.csv",
        loader=loader,
        partitioner=no_partitions,
        optimizer=search(),
        picture=False,
    )
    assert [o.status for o in outcomes] == [
        "failed",
        "no_design_found",
        "no_design_found",
        "no_design_found",
    ]
    assert len((tmp_path / "out" / "s.csv").read_text().strip().splitlines()) == 5
