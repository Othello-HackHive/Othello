"""Make the sample files for the frontend (T15).

    python tests/make_samples.py            # rewrites tests/data/samples/

Each folder looks like the output folder of one run (same file names as the
runner will use).  Everything is built from the practice houses and designs in
tests/fixtures, so the samples are deterministic: running this twice gives the
same bytes.  A test (tests/test_part2_samples.py) regenerates them and fails if
the committed files differ, so they cannot go stale.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]

from fixtures import designs
from fixtures.plans import slide5_plan, tiny_plan

from othello.checker import check_design
from othello.io.errors import NoDesignFoundError
from othello.io.failure import failure_from_exception, write_failure_json
from othello.io.official import write_official_json
from othello.io.overlay import write_overlay_json
from othello.io.paths import output_paths
from othello.models.result import HubCountStatus

SAMPLES_DIR = HERE / "data" / "samples"
FIXED_TIME = "2026-10-16T12:00:00Z"  # so the failure note is the same every time

PROVEN = HubCountStatus(lower_bound=2, tried={2: "feasible"}, proven_minimum=True)
TINY_PROVEN = HubCountStatus(lower_bound=1, tried={1: "feasible"}, proven_minimum=True)
UNRESOLVED = HubCountStatus(lower_bound=2, tried={2: "unresolved", 3: "unresolved"}, proven_minimum=False)


def build_samples(root: Path) -> list[Path]:
    """Write every sample under ``root``; return the files written (sorted)."""
    plan, tiny = slide5_plan(), tiny_plan()
    written: list[Path] = []

    def case(folder, plan, design, *, official=False, status=None, notes=(), failure=None):
        paths = output_paths(root / folder, plan.plan_id)
        validation = check_design(plan, design) if design is not None else None
        if official:
            write_official_json(plan, design, paths["official"])
            written.append(paths["official"])
        write_overlay_json(plan, design, validation, paths["overlay"], hub_count_status=status, notes=notes)
        written.append(paths["overlay"])
        if failure is not None:
            report = failure_from_exception(
                failure, plan_id=plan.plan_id, settings_changed={"time_budget_s": 60.0}, created_at=FIXED_TIME
            )
            write_failure_json(report, paths["failure"])
            written.append(paths["failure"])

    # the smallest possible plan: use it to wire up the frontend first
    case("tiny", tiny, designs.tiny_design(), official=True, status=TINY_PROVEN)
    # a good design: 2 hubs, 6 nodes, no crossings, no overlap
    case("valid", plan, designs.slide5_design(), official=True, status=PROVEN)
    # legal but costly: shows crossing markers and overlap stretches (still status "ok")
    case("costly", plan, designs.slide5_with_overlap(), official=True, status=PROVEN)
    # a broken design: drawn with red violations; NO official file is ever written for it
    case("invalid", plan, designs.broken_cable_through_wall(), status=PROVEN, notes=["example of a rule-breaking design"])
    # nothing found: just the house, the reason, and what was tried
    case(
        "no_design",
        plan,
        None,
        status=UNRESOLVED,
        notes=["no valid design found within the time budget (example)"],
        failure=NoDesignFoundError(
            "no valid design was found",
            notes=["time budget used up at 2 hubs", "3 hubs not finished either"],
        ),
    )
    return sorted(written)


def main() -> None:
    if SAMPLES_DIR.exists():
        for child in SAMPLES_DIR.iterdir():
            if child.is_dir():
                shutil.rmtree(child)  # remove stale samples; README.md is kept
    files = build_samples(SAMPLES_DIR)
    print(f"wrote {len(files)} files under {SAMPLES_DIR}")
    for path in files:
        print("  ", path.relative_to(SAMPLES_DIR).as_posix())


if __name__ == "__main__":
    main()
