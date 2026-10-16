"""The development audit must revisit the same plans after solver changes."""

import json

from audit_full_pipeline import _manifest, classify, select_ids


def test_fixed_hash_sample_includes_required_plan_and_freezes_manifest(tmp_path):
    records = [{"id": value} for value in ("9", "2", "6492", "7", "3")]
    selected = select_ids(records, 3)
    assert selected == select_ids(list(reversed(records)), 3)
    assert len(selected) == 3 and selected[0] == "6492"
    assert _manifest(records, tmp_path, 3) == selected
    assert _manifest([{"id": "new"}, *records], tmp_path, 4) == selected
    assert json.loads((tmp_path / "manifest.json").read_text())["plan_ids"] == selected


def test_audit_keeps_timeout_and_grid_disconnection_separate_from_checker_rejection():
    assert classify(["deadline expired; incumbent retained"], "NO_DESIGN_FOUND") == "timeout"
    assert classify(["unreachable_on_grid; geometric infeasibility is not proven"],
                    "NO_DESIGN_FOUND") == "grid_disconnection"
    assert classify(["Part 2 checker failed"], "NO_DESIGN_FOUND") == "checker_rejection"
