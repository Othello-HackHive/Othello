"""Try the runner yourself on a practice house.

    python tests/try_runner.py            # the 2-room house (about 10 seconds)
    python tests/try_runner.py house      # the 7-room house (about 20 seconds)

Results go to out/try_<name>/ (git ignores out/).  Open the .png to see the cables.
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path[:0] = [str(HERE), str(HERE.parent / "src")]

from fixtures.plans import slide5_plan, tiny_plan

from othello.models.config import SolverConfig
from othello.run import run_plan

HOUSES = {"tiny": tiny_plan, "house": slide5_plan}


def main(name: str) -> int:
    if name not in HOUSES:
        print(f"unknown house '{name}'; choose one of: {', '.join(HOUSES)}")
        return 2
    out = HERE.parent / "out" / f"try_{name}"
    outcome = run_plan(HOUSES[name](), out, SolverConfig(time_budget_s=20), console=True)
    print(f"\nstatus: {outcome.status}   hubs: {outcome.hubs}   score J: {outcome.metrics.get('J')}")
    print(f"files are in: {out}")
    for kind, path in outcome.files.items():
        print(f"  {kind:9s} {path.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1] if len(sys.argv) > 1 else "tiny"))
