"""Three-run synthetic Part 3 timing, kept outside timing-sensitive CI."""

from __future__ import annotations

import argparse
import json
import statistics
import time
from pathlib import Path
from typing import Any

from othello.models import SolverConfig
from othello.routing import RoutingCache
from othello.run import run_plan, safe_checker
from othello.solver import optimize_partitions
from tests.fixtures.plans import grid9_plan, l_plan, slide5_plan

HOUSES = {"slide5": (slide5_plan, 32.9), "grid9": (grid9_plan, 45.5),
          "lshape": (l_plan, 5.5)}


def benchmark(output: Path, runs: int = 3, budget: float = 60.0,
              houses: tuple[str, ...] | None = None) -> dict[str, Any]:
    if runs < 1 or budget <= 0:
        raise ValueError("runs and budget must be positive")
    output.mkdir(parents=True, exist_ok=True)
    report: dict[str, Any] = {}
    for name in houses or tuple(HOUSES):
        make_plan, old_first = HOUSES[name]
        samples = []
        for run_index in range(runs):
            plan = make_plan()
            config = SolverConfig(time_budget_s=budget)
            started = time.monotonic()
            first_verified = None
            cache = RoutingCache()

            def checker(check_plan, design, cfg, run_started=started):
                nonlocal first_verified
                result = safe_checker(check_plan, design, cfg)
                if result.valid and first_verified is None:
                    first_verified = time.monotonic() - run_started
                return result

            def optimizer(*args, target_cache=cache):
                supplied = list(args)
                supplied[4] = target_cache
                return optimize_partitions(*supplied)

            outcome = run_plan(plan, output / name / str(run_index + 1), config,
                               picture=False, checker=checker, optimizer=optimizer)
            stats = cache.stats()
            samples.append({"status": outcome.status, "first_verified_s": first_verified,
                            "runtime_s": time.monotonic() - started,
                            "J": outcome.metrics.get("J"), "hubs": outcome.hubs,
                            "graph_hits": stats.graph_hits, "graph_misses": stats.graph_misses,
                            "graph_builds": stats.graph_builds,
                            "graph_build_s": stats.graph_build_seconds,
                            "failure_code": outcome.failure.code if outcome.failure else None})
            (output / "benchmark.json").write_text(json.dumps(report | {name: {"runs": samples}}, indent=2)
                                                    + "\n", encoding="utf-8")
        firsts = [sample["first_verified_s"] for sample in samples if sample["first_verified_s"] is not None]
        report[name] = {"old_first_verified_s": old_first,
                        "first_verified_median_s": statistics.median(firsts) if len(firsts) == runs else None,
                        "runtime_median_s": statistics.median(sample["runtime_s"] for sample in samples),
                        "graph_build_median_s": statistics.median(sample["graph_build_s"] for sample in samples),
                        "runs": samples}
    (output / "benchmark.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    lines = ["# Part 3 three-run benchmark", "",
             f"Budget: {budget:g} s per plan. Times are wall-clock seconds.", ""]
    for name, item in report.items():
        lines.append(f"- {name}: first verified median {item['first_verified_median_s']}; "
                     f"old audit {item['old_first_verified_s']}; "
                     f"total median {item['runtime_median_s']:.2f}; "
                     f"statuses {[sample['status'] for sample in item['runs']]}")
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--time-budget", type=float, default=60.0)
    parser.add_argument("--house", action="append", choices=tuple(HOUSES))
    args = parser.parse_args()
    print(json.dumps(benchmark(args.out, args.runs, args.time_budget,
                               tuple(args.house) if args.house else None), indent=2))
