"""T19: the report-card tool (choosing plans, statistics, report files, command line)."""

import csv
import json
import pickle

import pytest
from fixtures import designs
from fixtures.plans import tiny_plan

from othello.harness import (
    PlanResult,
    format_report,
    indices_for_ids,
    main,
    normalise_reason,
    reason_of,
    recheck_written_answer,
    run_harness,
    select_indices,
    summarise,
)
from othello.io.failure import FailureReport
from othello.io.official import build_official
from othello.models.config import SolverConfig
from othello.run import RunOutcome, run_plan
from othello.solver.types import HubCountSummary, OuterSearchResult

CFG = SolverConfig(time_budget_s=10)


# --------------------------------------------------- choosing plans


def test_first_mode_takes_a_block_and_clamps_to_the_dataset():
    assert select_indices(10, 3) == [0, 1, 2]
    assert select_indices(10, 3, start=8) == [8, 9]
    assert select_indices(10, 5, start=20) == []
    assert select_indices(3, 99) == [0, 1, 2]


def test_random_mode_repeats_exactly_for_the_same_seed_only():
    first = select_indices(1000, 20, "random", seed=7)
    assert first == select_indices(1000, 20, "random", seed=7)
    assert first != select_indices(1000, 20, "random", seed=8)
    assert len(set(first)) == 20 and first == sorted(first) and max(first) < 1000
    assert select_indices(5, 50, "random", seed=1) == [0, 1, 2, 3, 4]


@pytest.mark.parametrize("bad", [{"mode": "weird"}, {"count": -1}, {"start": -1}])
def test_nonsense_selection_is_rejected(bad):
    with pytest.raises(ValueError):
        select_indices(10, **{"count": 3, **bad})


def test_plans_can_be_picked_by_id_in_the_order_asked():
    records = [{"id": 5}, {"id": 9}, {"id": "x"}]
    assert indices_for_ids(records, ["x", "5"]) == [2, 0]
    with pytest.raises(ValueError, match="no plan with id: 77"):
        indices_for_ids(records, ["9", "77"])


# --------------------------------------------------- reasons and statistics


def test_reasons_from_different_plans_look_the_same():
    a = normalise_reason(
        "H=2: F0 unsupported_geometry: T1: rooms R01 and R11 have ambiguous ownership"
    )
    b = normalise_reason(
        "H=3: F0 unsupported_geometry: T4: rooms R02 and R08 have ambiguous ownership"
    )
    assert a == b and "F0" in a and "R#" in a
    assert len(normalise_reason("x" * 500)) == 100


def make_outcome(
    plan_id,
    status="ok",
    hubs=None,
    bound=None,
    J=None,
    runtime=1.0,
    proven=False,
    notes=(),
    failure=None,
):
    outcome = RunOutcome(
        plan_id=plan_id, status=status, hubs=hubs, lower_bound=bound, proven_min=proven
    )
    outcome.runtime_s = runtime
    outcome.notes = list(notes)
    outcome.failure = failure
    if J is not None:
        outcome.metrics = {"L": J, "X": 0, "O": 0.0, "B": 0, "J": J}
    return outcome


def failure(code, message, notes=()):
    return FailureReport(
        code=code,
        stage="solve",
        message=message,
        details={"notes": list(notes)} if notes else {},
    )


def sample_results():
    return [
        PlanResult(
            make_outcome("a", hubs=2, bound=2, J=10.0, runtime=2.0, proven=True),
            recheck="passed",
        ),
        PlanResult(
            make_outcome(
                "b", hubs=3, bound=2, J=30.0, runtime=4.0, notes=["time ran out"]
            ),
            recheck="passed",
        ),
        PlanResult(
            make_outcome(
                "c",
                "no_design_found",
                runtime=6.0,
                failure=failure(
                    "NO_DESIGN_FOUND",
                    "nothing",
                    ["H=1: F0 unsupported_geometry: T1: rooms R01 and R02 overlap"],
                ),
            )
        ),
        PlanResult(
            make_outcome(
                "d",
                "no_design_found",
                runtime=8.0,
                failure=failure(
                    "NO_DESIGN_FOUND",
                    "nothing",
                    ["H=2: F0 unsupported_geometry: T3: rooms R05 and R09 overlap"],
                ),
            )
        ),
        PlanResult(
            make_outcome(
                "e", "failed", runtime=0.1, failure=failure("BAD_INPUT", "cannot read")
            )
        ),
    ]


def test_the_numbers_of_the_report_card_add_up():
    stats = summarise(sample_results())
    assert (stats["plans"], stats["ok"], stats["no_design_found"], stats["failed"]) == (
        5,
        2,
        2,
        1,
    )
    assert stats["solve_rate"] == pytest.approx(0.4)
    assert stats["hubs"] == {
        "at_lower_bound": 1,
        "above_lower_bound": 1,
        "mean_extra_hubs": 0.5,
        "proven_minimum": 1,
    }
    assert (
        stats["J"]["min"] == 10.0
        and stats["J"]["max"] == 30.0
        and stats["J"]["mean"] == 20.0
    )
    assert stats["runtime_s"]["max"] == 8.0 and stats["runtime_s"]["median"] == 4.0
    assert stats["ran_out_of_time"] == 1
    assert stats["recheck"] == {"passed": 2}


def test_failures_are_grouped_by_reason_with_example_plans():
    stats = summarise(sample_results())
    top = stats["failure_reasons"][0]
    assert top["plans"] == 2 and top["reason"].startswith(
        "NO_DESIGN_FOUND: H=#: F0 unsupported_geometry"
    )
    assert stats["failed_plans"][top["reason"]] == ["c", "d"]
    assert {item["reason"] for item in stats["failure_reasons"]} == {
        top["reason"],
        "BAD_INPUT: cannot read",
    }


def test_an_empty_batch_gives_a_report_without_dividing_by_zero():
    stats = summarise([])
    assert stats["plans"] == 0 and stats["solve_rate"] == 0.0 and stats["J"] is None
    text = format_report(stats)
    assert "plans run: 0" in text and "n/a" in text


def test_the_text_report_shows_the_key_numbers():
    text = format_report(summarise(sample_results()), {"time_budget_s": 10.0})
    for needle in (
        "plans run: 5",
        "time_budget_s",
        "verified design written :    2  (40%)",
        "above the lower bound: 1",
        "2 x NO_DESIGN_FOUND",
        "e.g. c, d",
        "1 x BAD_INPUT",
    ):
        assert needle in text, needle


def test_a_plan_with_no_failure_has_no_reason():
    assert reason_of(PlanResult(make_outcome("a", hubs=1, bound=1, J=1.0))) == ""


# --------------------------------------------------- the independent re-check


def fake_search(design):
    def optimizer(plan, batches, config, checker, cache, deadline, lower_bound):
        summary = HubCountSummary(lower_bound, {lower_bound: "feasible"}, True)
        return OuterSearchResult("verified", design, None, True, 1.0, summary, 1, 1, ())

    return optimizer


def no_partitions(plan, count, config, deadline):
    return [], "unresolved"


def tiny_runner(plan, out_dir, cfg, picture=False):
    return run_plan(
        plan,
        out_dir,
        cfg,
        picture=False,
        partitioner=no_partitions,
        optimizer=fake_search(designs.tiny_design()),
    )


def test_the_recheck_passes_for_a_good_written_answer(tmp_path):
    plan = tiny_plan()
    outcome = tiny_runner(plan, tmp_path, CFG)
    assert (
        outcome.status == "ok"
        and recheck_written_answer(plan, outcome, CFG) == "passed"
    )


def test_the_recheck_catches_an_answer_file_that_was_tampered_with(tmp_path):
    plan = tiny_plan()
    outcome = tiny_runner(plan, tmp_path, CFG)
    broken = build_official(plan, designs.broken_cable_through_wall(), CFG)
    outcome.files["official"].write_text(json.dumps(broken), encoding="utf-8")
    assert recheck_written_answer(plan, outcome, CFG) == "failed"
    outcome.files["official"].write_text("{not json", encoding="utf-8")
    assert recheck_written_answer(plan, outcome, CFG) == "unreadable"
    outcome.files["official"].write_text(json.dumps({"extra": 1}), encoding="utf-8")
    assert recheck_written_answer(plan, outcome, CFG) == "failed"


def test_the_recheck_has_nothing_to_do_without_an_answer():
    assert (
        recheck_written_answer(tiny_plan(), make_outcome("x", "failed"), CFG)
        == "not_applicable"
    )


# --------------------------------------------------- the whole batch


RECORDS = [{"id": "p1"}, {"id": "p2"}, {"id": "p3"}, {"id": "p4"}]


def builder(record, cfg):
    if record["id"] == "p2":
        raise ValueError("garbled plan")
    return tiny_plan()


def test_a_batch_survives_an_unreadable_plan_and_writes_every_report_file(tmp_path):
    lines = []
    results, stats = run_harness(
        RECORDS,
        [0, 1, 2],
        tmp_path,
        CFG,
        plan_builder=builder,
        runner=tiny_runner,
        progress=lines.append,
    )
    assert [r.outcome.status for r in results] == ["ok", "failed", "ok"]
    assert (
        results[1].outcome.plan_id == "p2"
        and results[1].outcome.failure.stage == "load"
    )
    assert [r.recheck for r in results] == ["passed", "not_applicable", "passed"]
    assert (
        stats["ok"] == 2
        and stats["failed"] == 1
        and len(lines) == 3
        and lines[0].startswith("[1/3]")
    )
    for name in ("report.txt", "report.json", "summary.csv"):
        assert (tmp_path / name).is_file()
    assert (tmp_path / "plans" / "TINY_PLAN.json").is_file()
    saved = json.loads((tmp_path / "report.json").read_text())
    assert saved["plans"] == 3 and saved["settings_changed"] == {"time_budget_s": 10.0}
    rows = list(csv.DictReader((tmp_path / "summary.csv").open(newline="")))
    assert [r["plan_id"] for r in rows] == ["TINY_PLAN", "p2", "TINY_PLAN"]
    assert "plans run: 3" in (tmp_path / "report.txt").read_text()


def test_only_the_chosen_plans_are_run(tmp_path):
    seen = []

    def spy_builder(record, cfg):
        seen.append(record["id"])
        return tiny_plan()

    run_harness(
        RECORDS, [3, 0], tmp_path, CFG, plan_builder=spy_builder, runner=tiny_runner
    )
    assert seen == ["p4", "p1"]


# --------------------------------------------------- the command line


def write_dataset(tmp_path, records=RECORDS):
    path = tmp_path / "data.pkl"
    path.write_bytes(pickle.dumps(list(records)))
    return path


def test_unknown_plan_ids_stop_the_command_with_code_2(tmp_path, capsys):
    data = write_dataset(tmp_path)
    assert main([str(data), "--ids", "nope", "--out", str(tmp_path / "o")], env={}) == 2
    assert "no plan with id" in capsys.readouterr().err


def test_a_missing_dataset_and_bad_settings_give_code_2(tmp_path, capsys):
    assert (
        main([str(tmp_path / "ghost.pkl"), "--out", str(tmp_path / "o")], env={}) == 2
    )
    data = write_dataset(tmp_path)
    assert (
        main([str(data), "--time-budget", "-3", "--out", str(tmp_path / "o")], env={})
        == 2
    )
    assert capsys.readouterr().err.count("error:") == 2


def test_a_dataset_of_unreadable_plans_still_gives_a_report_and_exit_0(
    tmp_path, capsys
):
    data = write_dataset(tmp_path, [{"id": 1}, {"id": 2}])  # no geometry at all
    out = tmp_path / "o"
    assert main([str(data), "--count", "2", "--out", str(out), "--quiet"], env={}) == 0
    report = json.loads((out / "report.json").read_text())
    assert report["plans"] == 2 and report["failed"] == 2 and report["ok"] == 0
    assert capsys.readouterr().out == ""


def test_an_output_folder_that_cannot_be_used_gives_code_1(tmp_path, capsys):
    data = write_dataset(tmp_path)
    blocker = tmp_path / "afile"
    blocker.write_text("x")
    assert main([str(data), "--out", str(blocker)], env={}) == 1
    assert "cannot use the output folder" in capsys.readouterr().err
