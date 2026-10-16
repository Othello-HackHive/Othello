"""The report-card tool (T19): run the whole pipeline on many plans and summarise how it did.

    python -m othello.harness data/resplan/ResPlan.pkl --count 50 --time-budget 30 --out out/harness
    python -m othello.harness data/resplan/ResPlan.pkl --count 20 --mode random --seed 7
    python -m othello.harness data/resplan/ResPlan.pkl --ids 14433 7067

Every plan goes through the real runner (``othello.run.run_plan``).  The harness adds three things:
choosing which plans to run, an independent re-check of every answer it wrote (rebuilt from the
official JSON file, the way the organisers' checker would read it), and one report of the whole batch.

Files written into ``--out``: ``report.txt`` (read this), ``report.json`` (the same numbers for
programs), ``summary.csv`` (one line per plan) and a ``plans/`` folder with every plan's own files.
"""

from __future__ import annotations

import argparse
import json
import math
import random
import re
import statistics
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from othello.checker import check_design
from othello.io.atomic import atomic_write_text
from othello.io.errors import OthelloError
from othello.io.failure import failure_from_exception
from othello.io.official import design_from_official, official_schema_problems
from othello.io.paths import safe_name
from othello.io.settings import (
    ConfigError,
    add_config_arguments,
    config_from_args,
    non_default_settings,
)
from othello.models.config import SolverConfig
from othello.run import RunOutcome, ensure_out_dir, run_plan, summary_text

MODES = ("first", "random")
REASON_LENGTH = 100
TIMEOUT_WORDS = ("deadline expired", "time ran out", "timeout")


# ------------------------------------------------------------ choosing plans


def select_indices(
    total: int,
    count: int,
    mode: str = "first",
    seed: int = 0,
    start: int = 0,
) -> list[int]:
    """Which plans to run: the first ``count`` from ``start``, or a seeded random sample.

    The same arguments always give the same list, so a run can be repeated exactly.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}")
    if count < 0 or start < 0:
        raise ValueError("count and start cannot be negative")
    if mode == "first":
        return list(range(min(start, total), min(start + count, total)))
    pool = list(range(total))
    return sorted(random.Random(seed).sample(pool, min(count, total)))


def indices_for_ids(
    records: Sequence[Mapping[str, Any]], ids: Sequence[str]
) -> list[int]:
    """Positions of the plans with these ids (in the order asked); unknown ids are an error."""
    position = {str(record.get("id")): index for index, record in enumerate(records)}
    unknown = [plan_id for plan_id in ids if str(plan_id) not in position]
    if unknown:
        raise ValueError(f"no plan with id: {', '.join(map(str, unknown))}")
    return [position[str(plan_id)] for plan_id in ids]


# ------------------------------------------------------------ one plan


class PlanResult:
    """The runner's outcome for one plan plus the harness's own independent re-check."""

    def __init__(
        self,
        outcome: RunOutcome,
        index: int | None = None,
        recheck: str = "not_applicable",
    ):
        self.outcome = outcome
        self.index = index
        # "passed" | "failed" | "not_applicable" (nothing was written) | "unreadable"
        self.recheck = recheck


def recheck_written_answer(plan: Any, outcome: RunOutcome, cfg: SolverConfig) -> str:
    """Read the official file back and run the checker on it, as an outsider would."""
    path = outcome.files.get("official")
    if outcome.status != "ok" or path is None:
        return "not_applicable"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        if official_schema_problems(data):
            return "failed"
        return (
            "passed"
            if check_design(plan, design_from_official(plan, data), cfg).valid
            else "failed"
        )
    except (OSError, ValueError, KeyError, TypeError, OthelloError):
        return "unreadable"


def _plan_label(record: Mapping[str, Any], index: int) -> str:
    return safe_name(record.get("id", f"plan_{index}"))


def run_one(
    record: Mapping[str, Any],
    index: int,
    out_dir: Path,
    cfg: SolverConfig,
    *,
    pictures: bool,
    plan_builder: Callable[..., Any],
    runner: Callable[..., RunOutcome],
) -> PlanResult:
    """Build the plan from a raw record and run it; a plan that cannot even be built is a failed row."""
    label = _plan_label(record, index)
    started = time.monotonic()
    try:
        plan = plan_builder(record, cfg)
    except Exception as problem:  # noqa: BLE001  one unreadable plan must never stop the batch
        report = failure_from_exception(
            problem,
            plan_id=label,
            stage="load",
            settings_changed=non_default_settings(cfg),
        )
        outcome = RunOutcome(
            plan_id=label,
            status="failed",
            runtime_s=time.monotonic() - started,
            failure=report,
        )
        return PlanResult(outcome, index)
    outcome = runner(plan, out_dir, cfg, picture=pictures)
    return PlanResult(outcome, index, recheck_written_answer(plan, outcome, cfg))


# ------------------------------------------------------------ the statistics


def normalise_reason(text: str) -> str:
    """Make failure reasons from different plans comparable (ids and numbers replaced)."""
    text = re.sub(r"\b[RNHDPT]\d+\b", lambda m: m.group(0)[0] + "#", text)
    text = re.sub(r"(?<![A-Za-z])\d+(\.\d+)?", "#", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:REASON_LENGTH]


def reason_of(result: PlanResult) -> str:
    """One short label for why a plan did not get a design."""
    outcome = result.outcome
    failure = outcome.failure
    if failure is None:
        return ""
    detail = ""
    if failure.code == "NO_DESIGN_FOUND":
        notes = failure.details.get("notes") or outcome.notes
        # Prefer the solver's own explanation over our generic sentence.
        detail = next(
            (
                n
                for n in notes
                if "unsupported_geometry" in n
                or "unreachable" in n
                or "no_candidates" in n
            ),
            "",
        )
        detail = detail or (notes[0] if notes else failure.message)
    else:
        detail = failure.message
    return f"{failure.code}: {normalise_reason(detail)}"


def _spread(values: Sequence[float]) -> dict[str, float] | None:
    if not values:
        return None
    ordered = sorted(values)
    ninetieth = ordered[min(len(ordered) - 1, math.ceil(0.9 * len(ordered)) - 1)]
    return {
        "min": ordered[0],
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p90": ninetieth,
        "max": ordered[-1],
    }


def summarise(results: Sequence[PlanResult]) -> dict[str, Any]:
    """All the numbers of the report card, as plain data."""
    outcomes = [r.outcome for r in results]
    ok = [r for r in results if r.outcome.status == "ok"]
    statuses = Counter(o.status for o in outcomes)
    gaps = [
        r.outcome.hubs - r.outcome.lower_bound
        for r in ok
        if r.outcome.hubs is not None and r.outcome.lower_bound is not None
    ]
    timed_out = [
        r
        for r in results
        if any(word in note for note in r.outcome.notes for word in TIMEOUT_WORDS)
    ]
    reasons = Counter(reason_of(r) for r in results if r.outcome.status != "ok")
    return {
        "plans": len(results),
        "ok": statuses.get("ok", 0),
        "no_design_found": statuses.get("no_design_found", 0),
        "failed": statuses.get("failed", 0),
        "solve_rate": (len(ok) / len(results)) if results else 0.0,
        "hubs": {
            "at_lower_bound": sum(1 for g in gaps if g == 0),
            "above_lower_bound": sum(1 for g in gaps if g > 0),
            "mean_extra_hubs": statistics.fmean(gaps) if gaps else None,
            "proven_minimum": sum(1 for r in ok if r.outcome.proven_min),
        },
        "J": _spread([r.outcome.metrics["J"] for r in ok if "J" in r.outcome.metrics]),
        "runtime_s": _spread([o.runtime_s for o in outcomes]),
        "ran_out_of_time": len(timed_out),
        "recheck": dict(
            Counter(r.recheck for r in results if r.outcome.status == "ok")
        ),
        "failure_reasons": [
            {"reason": reason, "plans": count}
            for reason, count in reasons.most_common()
        ],
        "failed_plans": {
            reason: [
                r.outcome.plan_id
                for r in results
                if r.outcome.status != "ok" and reason_of(r) == reason
            ][:10]
            for reason in reasons
        },
    }


def _percent(part: int, whole: int) -> str:
    return f"{100 * part / whole:.0f}%" if whole else "n/a"


def _spread_line(label: str, spread: dict[str, float] | None, unit: str = "") -> str:
    if spread is None:
        return f"  {label}: n/a"
    return (
        f"  {label}: min {spread['min']:.2f}{unit}   median {spread['median']:.2f}{unit}   "
        f"mean {spread['mean']:.2f}{unit}   90th pct {spread['p90']:.2f}{unit}   max {spread['max']:.2f}{unit}"
    )


def format_report(
    stats: Mapping[str, Any], settings_changed: Mapping[str, Any] | None = None
) -> str:
    """The report card as plain text."""
    total = stats["plans"]
    hubs = stats["hubs"]
    lines = [
        "REPORT CARD",
        "=" * 11,
        f"plans run: {total}",
        f"settings changed from defaults: {dict(settings_changed) if settings_changed else 'none'}",
        "",
        "RESULTS",
        f"  verified design written : {stats['ok']:>4}  ({_percent(stats['ok'], total)})",
        f"  no design found         : {stats['no_design_found']:>4}  ({_percent(stats['no_design_found'], total)})",
        f"  failed (error)          : {stats['failed']:>4}  ({_percent(stats['failed'], total)})",
        f"  ran out of time         : {stats['ran_out_of_time']:>4}  (some of these still gave a design)",
        "",
        "HUB COUNT (plans with a design)",
        f"  at the lower bound   : {hubs['at_lower_bound']}",
        f"  above the lower bound: {hubs['above_lower_bound']}"
        + (
            f"   (on average {hubs['mean_extra_hubs']:.2f} extra hubs)"
            if hubs["mean_extra_hubs"]
            else ""
        ),
        f"  proven minimum       : {hubs['proven_minimum']}",
        "",
        "COST J (plans with a design; lower is better)",
        _spread_line("J", stats["J"]),
        "",
        "TIME PER PLAN",
        _spread_line("seconds", stats["runtime_s"], " s"),
        "",
        "INDEPENDENT RE-CHECK (answer file read back and checked again)",
        f"  {stats['recheck'] or 'no answers written'}",
        "",
        "WHY PLANS DID NOT GET A DESIGN",
    ]
    if not stats["failure_reasons"]:
        lines.append("  (none)")
    for item in stats["failure_reasons"]:
        examples = ", ".join(stats["failed_plans"][item["reason"]][:5])
        lines.append(f"  {item['plans']:>4} x {item['reason']}")
        lines.append(f"         e.g. {examples}")
    return "\n".join(lines) + "\n"


# ------------------------------------------------------------ the whole batch


def run_harness(
    records: Sequence[Mapping[str, Any]],
    indices: Sequence[int],
    out_dir: str | Path,
    cfg: SolverConfig | None = None,
    *,
    pictures: bool = False,
    progress: Callable[[str], None] | None = None,
    plan_builder: Callable[..., Any] | None = None,
    runner: Callable[..., RunOutcome] = run_plan,
) -> tuple[list[PlanResult], dict[str, Any]]:
    """Run the chosen plans and write the report files.  Returns the results and the statistics."""
    config = cfg or SolverConfig()
    if plan_builder is None:
        from othello.reader.load_plan import (
            build_floor_plan_from_resplan as plan_builder,
        )
    folder = ensure_out_dir(out_dir)
    plans_dir = ensure_out_dir(folder / "plans")
    results: list[PlanResult] = []
    for done, index in enumerate(indices, start=1):
        result = run_one(
            records[index],
            index,
            plans_dir,
            config,
            pictures=pictures,
            plan_builder=plan_builder,
            runner=runner,
        )
        results.append(result)
        if progress:
            progress(f"[{done}/{len(indices)}] {_progress_line(result)}")
    stats = summarise(results)
    changed = non_default_settings(config)
    atomic_write_text(folder / "report.txt", format_report(stats, changed))
    atomic_write_text(
        folder / "report.json",
        json.dumps({"settings_changed": changed, **stats}, indent=2) + "\n",
    )
    atomic_write_text(
        folder / "summary.csv", summary_text([r.outcome for r in results])
    )
    return results, stats


def _progress_line(result: PlanResult) -> str:
    outcome = result.outcome
    if outcome.status == "ok":
        return f"{outcome.plan_id}: ok  hubs={outcome.hubs} (bound {outcome.lower_bound}) J={outcome.metrics['J']:.2f} {outcome.runtime_s:.1f}s recheck={result.recheck}"
    return f"{outcome.plan_id}: {outcome.status}  {reason_of(result)}  {outcome.runtime_s:.1f}s"


# ------------------------------------------------------------ command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m othello.harness",
        description="Run many plans and write a report card.",
    )
    parser.add_argument(
        "dataset",
        help="the ResPlan pickle (a list of plans), e.g. data/resplan/ResPlan.pkl",
    )
    parser.add_argument(
        "--count", type=int, default=10, help="how many plans to run (default 10)"
    )
    parser.add_argument(
        "--mode",
        choices=MODES,
        default="first",
        help="first N plans, or a random sample (default first)",
    )
    parser.add_argument(
        "--seed", type=int, default=0, help="seed for --mode random (default 0)"
    )
    parser.add_argument(
        "--start", type=int, default=0, help="where --mode first starts (default 0)"
    )
    parser.add_argument(
        "--ids", nargs="+", metavar="ID", help="run exactly these plan ids instead"
    )
    parser.add_argument(
        "--out",
        default="out/harness",
        help="folder for the report and per-plan files (default out/harness)",
    )
    parser.add_argument(
        "--pictures",
        action="store_true",
        help="also draw a PNG for every plan (slower)",
    )
    parser.add_argument(
        "--quiet", action="store_true", help="no progress lines on screen"
    )
    add_config_arguments(parser)
    return parser


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    """Exit code 0 when the batch ran (whatever the results), 2 for a bad command, 1 if results cannot be written."""
    args = build_parser().parse_args(argv)
    try:
        cfg = config_from_args(args, env)
        from othello.reader.load_plan import load_resplan_dataset

        records = load_resplan_dataset(args.dataset)
        indices = (
            indices_for_ids(records, args.ids)
            if args.ids
            else select_indices(
                len(records), args.count, args.mode, args.seed, args.start
            )
        )
    except (ConfigError, ValueError, OSError) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    say = None if args.quiet else (lambda line: print(line, flush=True))
    try:
        _, stats = run_harness(
            records, indices, args.out, cfg, pictures=args.pictures, progress=say
        )
    except OthelloError as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    if not args.quiet:
        print()
        print(format_report(stats, non_default_settings(cfg)))
        print(f"files are in: {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
