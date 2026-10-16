"""Reproducible, development-only Part 1 -> Part 3 -> Part 2 ResPlan audit.

    python audit_full_pipeline.py resplan/ResPlan.pkl --out audit_results

The selected IDs are frozen in ``manifest.json``. Subsequent runs reuse that
manifest, including after code changes, so before/after reports are comparable.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import statistics
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent / "src"))

from othello.checker import check_design
from othello.io.official import design_from_official
from othello.models import SolverConfig
from othello.reader import build_floor_plan_from_resplan, load_resplan_dataset
from othello.routing import RoutingCache
from othello.run import run_plan, safe_checker
from othello.solver import optimize_partitions
from othello.territory import generate_partitions


def select_ids(records: list[dict[str, Any]], count: int, required_id: str = "6492") -> list[str]:
    """Select stable distinct IDs without depending on dataset file order."""
    if count < 1:
        raise ValueError("count must be positive")
    ids = {str(record.get("id", "unknown")) for record in records}
    ranked = sorted(ids, key=lambda value: (hashlib.sha256(value.encode()).hexdigest(), value))
    if required_id in ids:
        return [required_id, *(value for value in ranked if value != required_id)][:count]
    return ranked[:count]


def _manifest(records: list[dict[str, Any]], output: Path, count: int) -> list[str]:
    path = output / "manifest.json"
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        ids = manifest["plan_ids"]
        if not isinstance(ids, list) or not all(isinstance(value, str) for value in ids):
            raise ValueError("manifest.json has invalid plan_ids")
        return ids
    ids = select_ids(records, count)
    path.write_text(json.dumps({"selection": "sha256(plan_id), with 6492 included when present",
                                "plan_ids": ids}, indent=2) + "\n", encoding="utf-8")
    return ids


def classify(notes: list[str], failure_code: str | None) -> str:
    joined = " ".join(notes).lower()
    if failure_code is None:
        return "success"
    if "unsupported_geometry" in joined:
        return "unsupported_geometry"
    if "timeout" in joined or "deadline" in joined or "time budget" in joined:
        return "timeout"
    if "checker failed" in joined or "no library combination passed" in joined or failure_code == "DESIGN_INVALID":
        return "checker_rejection"
    if "unreachable_on_grid" in joined or "no complete ring" in joined:
        return "grid_disconnection"
    return "other_failure"


def audit(dataset: Path, output: Path, count: int = 50, budget_s: float = 60.0) -> dict[str, Any]:
    if budget_s <= 0 or count <= 0:
        raise ValueError("count and budget_s must be positive")
    output.mkdir(parents=True, exist_ok=True)
    records = load_resplan_dataset(dataset)
    ids = _manifest(records, output, count)
    by_id = {str(record.get("id", "unknown")): record for record in records}
    config = SolverConfig(time_budget_s=budget_s)
    rows: list[dict[str, Any]] = []
    examples: dict[str, str] = {}

    for index, plan_id in enumerate(ids, 1):
        print(f"Full pipeline {index}/{len(ids)}: {plan_id}", file=sys.stderr, flush=True)
        started = time.monotonic()
        row: dict[str, Any] = {"plan_id": plan_id, "classification": "", "status": "",
                               "first_verified_s": None, "runtime_s": None,
                               "hubs": None, "J": None, "best_verified_j": None,
                               "final_not_worse_than_incumbent": None,
                               "graph_hits": 0, "graph_misses": 0,
                               "graph_builds": 0, "graph_build_s": 0.0,
                               "repairs": [], "diagnostics": [], "partition_stages": [],
                               "checker_rejection_counts": {}, "checker_examples": [],
                               "output_verified": False}
        record = by_id.get(plan_id)
        if record is None:
            row.update(classification="missing_record", status="failed",
                       diagnostics=["ID from manifest is absent from dataset"])
            rows.append(row)
            continue
        try:
            plan = build_floor_plan_from_resplan(record, config)
        except Exception as error:  # noqa: BLE001  record one bad source without aborting the corpus
            row.update(classification="load_failure", status="failed",
                       diagnostics=[f"{type(error).__name__}: {error}"],
                       runtime_s=round(time.monotonic() - started, 3))
            rows.append(row)
            examples.setdefault("load_failure", plan_id)
            continue
        row["repairs"] = list(plan.repairs)
        row["eligible_rooms"] = len(plan.eligible_rooms())
        row["load_s"] = round(time.monotonic() - started, 3)
        remaining_s = budget_s - (time.monotonic() - started)
        if remaining_s <= 0:
            row.update(classification="timeout", status="failed", runtime_s=row["load_s"],
                       diagnostics=["Reader used the full per-plan budget"])
            rows.append(row)
            examples.setdefault("timeout", plan_id)
            continue
        run_config = config.model_copy(update={"time_budget_s": remaining_s})
        route_cache = RoutingCache()

        def tracked_checker(check_plan: Any, design: Any, cfg: Any,
                            target_row: dict[str, Any] = row, run_started: float = started) -> Any:
            result = safe_checker(check_plan, design, cfg)
            if result.valid and result.metrics is not None:
                if target_row["first_verified_s"] is None:
                    target_row["first_verified_s"] = round(time.monotonic() - run_started, 3)
                prior = target_row["best_verified_j"]
                target_row["best_verified_j"] = (result.metrics.J if prior is None
                                                 else min(prior, result.metrics.J))
            elif not result.valid:
                counts = target_row["checker_rejection_counts"]
                for violation in result.violations:
                    counts[violation.type] = counts.get(violation.type, 0) + 1
                    if len(target_row["checker_examples"]) < 3:
                        target_row["checker_examples"].append(
                            f"{violation.type}: {violation.detail}")
            return result

        def tracked_optimizer(*args: Any, target_cache: RoutingCache = route_cache) -> Any:
            supplied = list(args)
            supplied[4] = target_cache
            return optimize_partitions(*supplied)

        def tracked_partitioner(partition_plan: Any, hub_count: int, cfg: Any,
                                deadline: float, target_row: dict[str, Any] = row) -> Any:
            options, status = generate_partitions(partition_plan, hub_count, cfg, deadline)
            target_row["partition_stages"].append({"hubs": hub_count,
                                                    "options": len(options), "status": status})
            return options, status

        try:
            outcome = run_plan(plan, output / "plans" / plan_id, run_config, picture=False,
                               checker=tracked_checker, optimizer=tracked_optimizer,
                               partitioner=tracked_partitioner)
            row["status"] = outcome.status
            row["hubs"] = outcome.hubs
            row["J"] = outcome.metrics.get("J")
            row["diagnostics"] = outcome.notes[:8]
            failure_code = outcome.failure.code if outcome.failure else None
            row["failure_code"] = failure_code
            row["failure_message"] = outcome.failure.message if outcome.failure else None
            row["classification"] = classify(outcome.notes, failure_code)
            if row["classification"] == "other_failure" and row["partition_stages"] and all(
                    stage["options"] == 0 for stage in row["partition_stages"]):
                row["classification"] = "no_partition"
            if outcome.status == "ok":
                row["final_not_worse_than_incumbent"] = (
                    row["best_verified_j"] is not None and row["J"] <= row["best_verified_j"] + 1e-8)
                data = json.loads(outcome.files["official"].read_text(encoding="utf-8"))
                independent = check_design(plan, design_from_official(plan, data), run_config)
                row["output_verified"] = bool(independent.valid)
                if not independent.valid:
                    row["classification"] = "checker_rejection"
                    row["diagnostics"].append("Independent official-output recheck failed")
                if not row["final_not_worse_than_incumbent"]:
                    row["classification"] = "incumbent_regression"
                    row["diagnostics"].append("Final J exceeds a verified incumbent from this run")
        except Exception as error:  # noqa: BLE001  a harness error must not erase other plan results
            row["classification"] = "harness_error"
            if row["status"] != "ok":
                row["status"] = "failed"
            row["diagnostics"].append(f"{type(error).__name__}: {error}")
        stats = route_cache.stats()
        row["graph_hits"], row["graph_misses"] = stats.graph_hits, stats.graph_misses
        row["graph_builds"] = stats.graph_builds
        row["graph_build_s"] = round(stats.graph_build_seconds, 3)
        row["runtime_s"] = round(time.monotonic() - started, 3)
        examples.setdefault(row["classification"], plan_id)
        rows.append(row)
        (output / "results.json").write_text(json.dumps(rows, indent=2) + "\n", encoding="utf-8")

    counts = Counter(row["classification"] for row in rows)
    checker_rejections = Counter()
    for row in rows:
        checker_rejections.update(row["checker_rejection_counts"])
    successful = [row for row in rows if row["classification"] == "success"]
    summary = {"dataset": str(dataset), "time_budget_s": budget_s,
               "plans_selected": len(ids), "classifications": dict(sorted(counts.items())),
               "examples": examples, "invalid_official_outputs": sum(
                   row["status"] == "ok" and not row["output_verified"] for row in rows),
               "incumbent_regressions": sum(row["final_not_worse_than_incumbent"] is False
                                            for row in rows),
               "total_graph_builds": sum(row["graph_builds"] for row in rows),
               "checker_rejection_counts": dict(checker_rejections.most_common()),
               "first_verified_median_s": (statistics.median(row["first_verified_s"]
                                           for row in successful) if successful else None),
               "runtime_median_s": statistics.median(row["runtime_s"]
                                                     for row in rows if row["runtime_s"] is not None)}
    (output / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    with (output / "results.csv").open("w", newline="", encoding="utf-8") as target:
        columns = ("plan_id", "classification", "status", "eligible_rooms", "load_s",
                   "first_verified_s",
                   "runtime_s", "hubs", "J", "best_verified_j",
                   "final_not_worse_than_incumbent", "graph_builds", "graph_build_s",
                   "graph_hits", "graph_misses", "output_verified")
        writer = csv.DictWriter(target, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)
    report = ["# Full-pipeline ResPlan audit", "", f"Budget: {budget_s:g} s per plan; {len(ids)} selected.", "",
              "## Results", ""]
    for name, value in sorted(counts.items()):
        example = next(row for row in rows if row["classification"] == name)
        reason = (example["diagnostics"][0] if example["diagnostics"] else
                  example.get("failure_message") or str(example["partition_stages"]))
        report.append(f"- {name}: {value}; example {example['plan_id']}: {reason}")
    report.extend(["", f"Independently invalid official outputs: {summary['invalid_official_outputs']}.", "",
                   f"Final J regressions against same-run incumbents: {summary['incumbent_regressions']}.",
                   (f"Median total time: {summary['runtime_median_s']:.2f} s; "
                    f"completed graph builds: {summary['total_graph_builds']}."), "",
                   f"Most common checker violations: {checker_rejections.most_common(5)}.", "",
                   "See `manifest.json` and `results.json` for IDs, timings, repairs and diagnostics.", ""])
    (output / "report.md").write_text("\n".join(report), encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--count", type=int, default=50)
    parser.add_argument("--time-budget", type=float, default=60.0)
    args = parser.parse_args()
    print(json.dumps(audit(args.dataset, args.out, args.count, args.time_budget), indent=2))


if __name__ == "__main__":
    main()
