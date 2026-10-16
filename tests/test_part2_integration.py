"""T20: the real pieces working together (Part 1 reader -> Part 3 solver -> Part 2 checker, writers, runner).

Nothing here is faked: a raw ResPlan-style record goes in, files come out, and the answer file
is read back and checked again.  The last tests use the real dataset when it is on this machine.
"""

import json
import pickle
from pathlib import Path

import networkx as nx
import pytest
from shapely.geometry import MultiPolygon, box

from othello.checker import check_design, coerce_design, score_design
from othello.harness import main as harness_main
from othello.harness import run_harness
from othello.io.failure import read_failure_json
from othello.io.official import design_from_official, official_schema_problems
from othello.io.overlay import overlay_schema_problems
from othello.models.config import SolverConfig
from othello.reader.load_plan import (
    build_floor_plan,
    build_floor_plan_from_resplan,
    load_resplan_dataset,
)
from othello.run import main as run_main
from othello.run import run_plan

DATASET = Path(__file__).resolve().parents[1] / "data" / "resplan" / "ResPlan.pkl"
FAST = SolverConfig(time_budget_s=8)


def one_room_record(plan_id="ONE_ROOM", overlapping_walls=False):
    """An 8 m x 8 m room with a wall all round it, written the way raw ResPlan records are."""
    if overlapping_walls:
        walls = MultiPolygon(
            [
                box(-2, -2, 82, 0),
                box(-2, 80, 82, 82),
                box(-2, 0, 0, 80),
                box(80, 0, 82, 80),
            ]
        )
    else:
        walls = MultiPolygon([box(-2, -2, 82, 82).difference(box(0, 0, 80, 80))])
    return {
        "id": plan_id,
        "area": 64.0,
        "inner": box(0, 0, 80, 80),
        "wall": walls,
        "window": MultiPolygon([]),
        "door": MultiPolygon([]),
        "living": box(0, 0, 80, 80),
        "graph": nx.Graph(),
    }


def read_back(plan, official_path):
    data = json.loads(Path(official_path).read_text(encoding="utf-8"))
    assert official_schema_problems(data) == []
    return design_from_official(plan, data)


# --------------------------------------------------- a record through every real piece


def test_a_raw_record_becomes_a_checked_answer_and_every_file(tmp_path):
    plan = build_floor_plan_from_resplan(one_room_record())
    outcome = run_plan(plan, tmp_path, FAST)
    assert outcome.status == "ok" and outcome.hubs == 1 and outcome.lower_bound == 1
    for kind in ("official", "overlay", "picture", "log", "settings"):
        assert outcome.files[kind].is_file(), kind
    assert outcome.files["picture"].read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"
    assert (
        overlay_schema_problems(
            json.loads(outcome.files["overlay"].read_text(encoding="utf-8"))
        )
        == []
    )
    # An outsider reading the official file gets a design the checker accepts, with the same score.
    design = read_back(plan, outcome.files["official"])
    result = check_design(plan, design, FAST)
    assert result.valid and result.metrics.J == pytest.approx(outcome.metrics["J"])


def test_the_command_line_runs_a_pickled_record_end_to_end(tmp_path, capsys):
    source = tmp_path / "one_room.pkl"
    source.write_bytes(pickle.dumps(one_room_record()))
    out = tmp_path / "out"
    code = run_main(
        [
            str(source),
            "--out",
            str(out),
            "--time-budget",
            "8",
            "--summary",
            str(out / "s.csv"),
        ],
        env={},
    )
    assert code == 0 and "ONE_ROOM: ok" in capsys.readouterr().out
    plan = build_floor_plan(source)
    assert check_design(plan, read_back(plan, out / "ONE_ROOM.json"), FAST).valid
    assert (
        (out / "s.csv").read_text().splitlines()[1].startswith("ONE_ROOM,ok,1,1,true")
    )


def test_the_harness_runs_the_real_chain_and_its_recheck_agrees(tmp_path):
    records = [one_room_record("A"), one_room_record("B")]
    results, stats = run_harness(records, [0, 1], tmp_path, FAST)
    assert stats["ok"] == 2 and stats["solve_rate"] == 1.0
    assert [r.recheck for r in results] == ["passed", "passed"]
    assert json.loads((tmp_path / "report.json").read_text())["ok"] == 2


def test_the_harness_command_line_reads_a_dataset_file(tmp_path, capsys):
    data = tmp_path / "data.pkl"
    data.write_bytes(pickle.dumps([one_room_record("A"), one_room_record("B")]))
    code = harness_main(
        [str(data), "--ids", "B", "--out", str(tmp_path / "o"), "--time-budget", "8"],
        env={},
    )
    assert code == 0
    output = capsys.readouterr().out
    assert "B: ok" in output and "REPORT CARD" in output
    assert (tmp_path / "o" / "plans" / "B.json").is_file() and not (
        tmp_path / "o" / "plans" / "A.json"
    ).exists()


def test_a_design_passes_between_the_parts_as_a_plain_dict_or_a_model(tmp_path):
    plan = build_floor_plan_from_resplan(one_room_record())
    outcome = run_plan(plan, tmp_path, FAST, picture=False)
    design = read_back(plan, outcome.files["official"])
    as_dict = design.model_dump()
    assert coerce_design(as_dict) == design
    assert check_design(plan, as_dict, FAST).valid and score_design(
        plan, as_dict, FAST
    ) == score_design(plan, design, FAST)


def test_garbage_between_the_parts_is_an_invalid_result_not_a_crash():
    plan = build_floor_plan_from_resplan(one_room_record())
    for garbage in ({}, {"plan_id": 3}, {"hubs": "x"}, [], None, "design"):
        result = check_design(plan, garbage, FAST)
        assert result.valid is False and result.violations[0].type == "OUTPUT_SCHEMA"


@pytest.mark.xfail(
    reason="main's reader turns overlapping wall parts into a GeometryCollection that Part 3 rejects "
    "(fixed on Aadith's branch); starts passing by itself once that is merged",
    strict=False,
)
def test_overlapping_wall_pieces_do_not_stop_a_plan_from_being_solved(tmp_path):
    plan = build_floor_plan_from_resplan(one_room_record(overlapping_walls=True))
    assert run_plan(plan, tmp_path, FAST, picture=False).status == "ok"


# --------------------------------------------------- the real dataset (skipped when it is not here)


needs_dataset = pytest.mark.skipif(
    not DATASET.is_file(), reason="data/resplan/ResPlan.pkl is not on this machine"
)


@pytest.fixture(scope="module")
def real_records():
    return load_resplan_dataset(DATASET)


@needs_dataset
def test_the_dataset_loads_and_its_first_plans_become_floor_plans(real_records):
    assert len(real_records) > 1000
    for record in real_records[:10]:
        plan = build_floor_plan_from_resplan(record)
        assert plan.rooms and plan.nodes and plan.plan_id == str(record["id"])


@needs_dataset
def test_real_plans_never_crash_the_pipeline_and_never_leave_an_unchecked_answer(
    real_records, tmp_path
):
    results, stats = run_harness(
        real_records, [0, 1, 2], tmp_path, SolverConfig(time_budget_s=3)
    )
    assert (
        stats["plans"] == 3
        and stats["ok"] + stats["no_design_found"] + stats["failed"] == 3
    )
    for result in results:
        outcome = result.outcome
        official = tmp_path / "plans" / f"{outcome.plan_id}.json"
        if outcome.status == "ok":
            assert result.recheck == "passed" and official.is_file()
        else:
            assert (
                not official.exists()
                and not (tmp_path / "plans" / f"{outcome.plan_id}.png").exists()
            )
            note = read_failure_json(
                tmp_path / "plans" / f"{outcome.plan_id}.failure.json"
            )
            assert note.code and note.message and note.hint
    assert (tmp_path / "report.txt").is_file() and (tmp_path / "summary.csv").is_file()
