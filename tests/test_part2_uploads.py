"""The sample upload files stay in step with the code, and every one of them can be read."""

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from make_uploads import OUT, files

from othello.service.plan_json import plan_from_json

ROOT = Path(__file__).resolve().parents[1]


def test_the_committed_upload_samples_match_what_the_generator_writes():
    expected = files()
    assert {
        p.name for p in OUT.iterdir() if p.is_file() and p.name != ".gitattributes"
    } == set(expected)
    for name, text in expected.items():
        assert (OUT / name).read_text(encoding="utf-8").replace("\r\n", "\n") == text, (
            name
        )


@pytest.mark.parametrize("name", ["one_room.json", "three_rooms.json"])
def test_each_sample_upload_becomes_a_floor_plan(name):
    plan = plan_from_json((OUT / name).read_bytes())
    assert plan.rooms and plan.nodes
    assert json.loads((OUT / name).read_text(encoding="utf-8"))["inner"]["type"] in {
        "Polygon",
        "MultiPolygon",
    }


def test_the_three_room_sample_has_two_doors_between_its_rooms():
    plan = plan_from_json((OUT / "three_rooms.json").read_bytes())
    assert (
        sorted(o.kind for o in plan.openings.values()) == ["door", "door"]
        and len(plan.rooms) == 3
    )


def read_text(path):
    return path.read_text(encoding="utf-8").replace(chr(13) + chr(10), chr(10))


def test_the_generator_can_be_run_as_a_script_and_writes_the_same_files(tmp_path):
    copy = tmp_path / "tests"
    shutil.copytree(
        ROOT / "tests", copy, ignore=shutil.ignore_patterns("__pycache__", "data")
    )
    result = subprocess.run(
        [sys.executable, str(copy / "make_uploads.py")],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    assert result.returncode == 0 and "wrote 3 files" in result.stdout
    for name in files():
        assert read_text(copy / "data" / "uploads" / name) == read_text(OUT / name), (
            name
        )


def test_git_does_not_ignore_the_sample_uploads():
    out = subprocess.run(
        ["git", "check-ignore", "-q", str(OUT / "one_room.json")], cwd=ROOT, check=False
    )
    assert out.returncode == 1  # 1 means "not ignored"
