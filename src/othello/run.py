"""The runner (T16): load a plan, solve it, check it, write every output file.

    python -m othello.run path/to/plan.pkl --out out/ --time-budget 60
    python -m othello.run path/to/plans/   --out out/ --summary out/summary.csv

Part 2 owns this file; it only calls the public functions of Part 1 (load, partitions)
and Part 3 (the search).  Rules it keeps:

* Only a design the checker accepts is ever written as the official answer.  The
  solver's ``provisional_design`` is never submitted.
* A search that ran out of time but holds a verified design still counts as a success.
* Anything that goes wrong for one plan becomes a ``<plan_id>.failure.json`` and the
  batch carries on with the next plan.
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from othello.checker import check_design
from othello.checker.validate import coerce_design
from othello.io.atomic import atomic_write_text
from othello.io.errors import NoDesignFoundError, OthelloError, OutputWriteError
from othello.io.failure import FailureReport, failure_from_exception, write_failure_json
from othello.io.official import write_official_json
from othello.io.overlay import write_overlay_json
from othello.io.paths import output_paths, safe_name
from othello.io.picture import write_plan_picture
from othello.io.runlog import log_failure, log_settings, run_logger
from othello.io.settings import (
    ConfigError,
    add_config_arguments,
    config_from_args,
    non_default_settings,
    write_config,
)
from othello.models.config import SolverConfig
from othello.models.result import HubCountStatus, RuleCheck, ValidationResult, Violation

EXTRA_HUB_COUNTS = 2  # hub counts tried above the lower bound
PLAN_SUFFIXES = (".pkl", ".pickle")
SUMMARY_COLUMNS = (
    "plan_id",
    "status",
    "hubs",
    "lower_bound",
    "proven_min",
    "L",
    "X",
    "O",
    "B",
    "J",
    "runtime_s",
    "notes",
)


@dataclass
class RunOutcome:
    """What happened to one plan (also one row of the batch summary)."""

    plan_id: str
    status: str  # "ok" | "no_design_found" | "failed"
    source: str = ""
    hubs: int | None = None
    lower_bound: int | None = None
    proven_min: bool | None = None
    metrics: dict[str, float] = field(default_factory=dict)
    runtime_s: float = 0.0
    notes: list[str] = field(default_factory=list)
    files: dict[str, Path] = field(default_factory=dict)
    failure: FailureReport | None = None

    def summary_row(self) -> dict[str, Any]:
        row: dict[str, Any] = {
            "plan_id": self.plan_id,
            "status": self.status,
            "hubs": "" if self.hubs is None else self.hubs,
            "lower_bound": "" if self.lower_bound is None else self.lower_bound,
            "proven_min": ""
            if self.proven_min is None
            else str(self.proven_min).lower(),
            "runtime_s": f"{self.runtime_s:.2f}",
            "notes": "; ".join(
                self.notes + ([self.failure.code] if self.failure else [])
            ),
        }
        for key in ("L", "X", "O", "B", "J"):
            row[key] = self.metrics.get(key, "")
        return row


# ------------------------------------------------------------ safe pieces


def safe_checker(plan: Any, design: Any, cfg: Any) -> ValidationResult:
    """The checker, but an internal crash counts as "invalid" instead of killing the run."""
    try:
        return check_design(plan, design, cfg)
    except Exception as problem:  # noqa: BLE001  a checker bug must never let an unchecked design through
        return ValidationResult(
            valid=False,
            violations=[
                Violation(
                    type="OUTPUT_SCHEMA",
                    detail=f"checker crashed: {type(problem).__name__}: {problem}",
                )
            ],
            checks=[
                RuleCheck(
                    rule_id="schema",
                    label="OUTPUT FORMAT",
                    passed=False,
                    value="1 PROBLEMS",
                )
            ],
        )


def load_plan_file(path: str | Path, cfg: SolverConfig) -> Any:
    """Part 1's loader (a pickled ResPlan record for now; organiser JSON will plug in here)."""
    from othello.reader.load_plan import build_floor_plan

    return build_floor_plan(path, cfg)


def ensure_out_dir(out_dir: str | Path) -> Path:
    """Make sure the result folder exists and can be written to; otherwise say so clearly.

    This is the one problem that is not about a single plan, so it is raised (not turned
    into a failure note): there is nowhere to put a note.
    """
    folder = Path(out_dir)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / ".write_test.tmp"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as problem:
        raise OutputWriteError(
            f"cannot use the output folder {folder}: {problem}"
        ) from problem
    return folder


def find_plan_files(source: str | Path) -> list[Path]:
    """One plan file, or every plan file in a folder (sorted, so runs repeat exactly)."""
    path = Path(source)
    if path.is_dir():
        return sorted(
            p
            for p in path.iterdir()
            if p.is_file() and p.suffix.lower() in PLAN_SUFFIXES
        )
    if path.is_file():
        return [path]
    raise FileNotFoundError(f"no such plan file or folder: {path}")


def _partition_batches(
    plan: Any,
    lower_bound: int,
    cfg: SolverConfig,
    deadline: float,
    partitioner: Callable,
) -> dict:
    eligible = len(plan.eligible_rooms())
    batches: dict[int, tuple[list, str]] = {}
    for count in range(lower_bound, min(eligible, lower_bound + EXTRA_HUB_COUNTS) + 1):
        if time.monotonic() >= deadline:
            break
        batches[count] = partitioner(plan, count, cfg, deadline)
    return batches


def _too_big_room(plan: Any, cfg: SolverConfig) -> str | None:
    for room in plan.eligible_rooms():
        if room.area_m2 > cfg.max_area_per_hub_m2 + cfg.tolerance_m:
            return f"room {room.room_id} is {room.area_m2:.1f} m2, over the {cfg.max_area_per_hub_m2:g} m2 limit of one hub"
    return None


# ------------------------------------------------------------ one plan


def run_plan(
    source: str | Path | Any,
    out_dir: str | Path,
    cfg: SolverConfig | None = None,
    *,
    console: bool = False,
    picture: bool = True,
    loader: Callable[[Any, SolverConfig], Any] = load_plan_file,
    partitioner: Callable | None = None,
    optimizer: Callable | None = None,
    checker: Callable = safe_checker,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> RunOutcome:
    """Run one plan from start to finish.  Never raises for a plan-level problem.

    ``source`` is a plan file path, or an already-built ``FloorPlan``.
    The keyword arguments let tests (and the API) swap in other pieces.
    ``progress`` is called with a small dictionary at each step (see ``PROGRESS_STAGES``);
    a progress function that fails is logged and ignored, never allowed to stop the run.
    """
    config = cfg or SolverConfig()
    started = time.monotonic()
    ensure_out_dir(out_dir)
    name_hint = (
        Path(source).stem
        if isinstance(source, str | Path)
        else getattr(source, "plan_id", None)
    )
    paths = output_paths(out_dir, name_hint)
    outcome = RunOutcome(
        plan_id=safe_name(name_hint),
        status="failed",
        source=str(source) if isinstance(source, str | Path) else "",
    )
    plan: Any = None
    stage = "load"
    with run_logger(outcome.plan_id, paths["log"], console=console) as log:
        emit = _make_emitter(progress, started, log)
        emit("started", "Run started")
        try:
            log_settings(log, config)
            plan = (
                source if not isinstance(source, str | Path) else loader(source, config)
            )
            # From here on the files are named after the plan's own id, as the frontend expects.
            outcome.plan_id = safe_name(plan.plan_id)
            paths = {**output_paths(out_dir, plan.plan_id), "log": paths["log"]}
            write_config(config, paths["settings"])
            outcome.files["settings"] = paths["settings"]
            log.info(
                "loaded plan %s: %d rooms, %d nodes",
                plan.plan_id,
                len(plan.rooms),
                len(plan.nodes),
            )
            for repair in plan.repairs:
                log.info("repair: %s", repair)
            emit(
                "loaded",
                f"Plan loaded: {len(plan.rooms)} rooms, {len(plan.nodes)} nodes",
                plan_id=outcome.plan_id,
                rooms=len(plan.rooms),
                nodes=len(plan.nodes),
            )

            stage = "solve"
            _solve_and_write(
                plan,
                config,
                outcome,
                paths,
                log,
                started,
                picture,
                partitioner,
                optimizer,
                checker,
                emit,
            )
        except Exception as problem:  # noqa: BLE001  every plan-level problem becomes a failure note
            _record_failure(problem, plan, config, outcome, paths, log, stage, picture)
        outcome.runtime_s = time.monotonic() - started
        log.info("finished: %s in %.2f s", outcome.status, outcome.runtime_s)
        emit(
            "done",
            _done_message(outcome),
            status=outcome.status,
            hubs=outcome.hubs,
            J=outcome.metrics.get("J"),
            code=outcome.failure.code if outcome.failure else None,
        )
    outcome.files["log"] = paths["log"]
    return outcome


PROGRESS_STAGES = (
    "started",
    "loaded",
    "bound",
    "searching",
    "checking",
    "writing",
    "done",
)


def _make_emitter(progress, started, log):
    """One function that reports a step; a broken listener never stops the run."""

    def emit(stage: str, message: str, **data: Any) -> None:
        if progress is None:
            return
        try:
            progress(
                {
                    "stage": stage,
                    "message": message,
                    "elapsed_s": round(time.monotonic() - started, 2),
                    **data,
                }
            )
        except Exception as problem:  # noqa: BLE001  a listener must never be able to break a run
            log.warning("progress listener failed: %s", problem)

    return emit


def _done_message(outcome: RunOutcome) -> str:
    if outcome.status == "ok":
        return f"Done: a checked design with {outcome.hubs} hub(s) was found"
    if outcome.failure is not None:
        return f"Done: no design ({outcome.failure.message})"
    return f"Done: {outcome.status}"


def _solve_and_write(
    plan,
    config,
    outcome,
    paths,
    log,
    started,
    picture,
    partitioner,
    optimizer,
    checker,
    emit,
) -> None:
    if partitioner is None or optimizer is None:
        from othello.solver import optimize_partitions
        from othello.territory import generate_partitions

        partitioner = partitioner or generate_partitions
        optimizer = optimizer or optimize_partitions

    from othello.territory import hub_lower_bound

    lower_bound = hub_lower_bound(plan, config)
    outcome.lower_bound = lower_bound
    log.info("hub lower bound: %d", lower_bound)
    emit("bound", f"At least {lower_bound} hub(s) are needed", lower_bound=lower_bound)
    too_big = _too_big_room(plan, config)
    if too_big:
        raise NoDesignFoundError(too_big, notes=[too_big])

    budget = config.time_budget_s
    search_deadline = started + budget * (1.0 - config.final_check_reserve_fraction)
    if budget * (1.0 - config.final_check_reserve_fraction) <= time.get_clock_info("monotonic").resolution:
        raise NoDesignFoundError(
            "time budget is below the monotonic clock resolution",
            notes=["time budget used up in setup"],
        )
    batches = _partition_batches(
        plan, lower_bound, config, search_deadline, partitioner
    )
    if not batches:
        raise NoDesignFoundError(
            "no hub counts could be tried before the time budget ended",
            notes=["time budget used up in setup"],
        )
    log.info("hub counts tried: %s", sorted(batches))
    emit(
        "searching",
        f"Searching for the best cable design (up to {budget:g} s)",
        hub_counts=sorted(batches),
        budget_s=budget,
    )

    result = optimizer(
        plan, batches, config, checker, None, search_deadline, lower_bound
    )
    for line in dict.fromkeys(
        result.diagnostics
    ):  # the solver repeats itself; keep each message once
        log.info("solver: %s", line)
        outcome.notes.append(line)
    summary = result.hub_count_status
    hub_status = HubCountStatus(
        lower_bound=lower_bound,
        tried=dict(summary.tried),
        proven_minimum=summary.proven_minimum,
    )
    outcome.proven_min = summary.proven_minimum
    # Only a verified design counts, even when the search ended on a timeout.
    if result.design is None or not result.verified:
        if result.provisional_design is not None:
            log.warning(
                "a provisional design exists but it was never verified; it is NOT submitted"
            )
        raise NoDesignFoundError(
            f"search ended with status {result.status} and no verified design",
            notes=list(outcome.notes),
        )

    emit("checking", "Search finished; checking the design once more")
    design = coerce_design(result.design)
    validation = checker(plan, design, config)  # final, independent re-check
    if not validation.valid or validation.metrics is None:
        codes = sorted({v.type for v in validation.violations})
        raise NoDesignFoundError(
            f"the final check rejected the solver's design: {codes}",
            notes=list(outcome.notes),
        )

    if result.status == "timeout":
        outcome.notes.append("time ran out; best verified design kept")
    outcome.notes.append(
        f"{result.partitions_considered} partitions and {result.configurations_considered} configurations checked"
    )
    runtime = time.monotonic() - started
    emit("writing", "Writing the result files")
    write_official_json(plan, design, paths["official"], config)
    outcome.files["official"] = paths["official"]
    write_overlay_json(
        plan,
        design,
        validation,
        paths["overlay"],
        hub_count_status=hub_status,
        runtime_s=runtime,
        notes=list(outcome.notes),
        cfg=config,
        status="ok",
    )
    outcome.files["overlay"] = paths["overlay"]
    if picture:
        _try_picture(plan, design, validation, paths, outcome, log, config)
    _remove(paths["failure"], log)  # an old failure note would contradict this success
    metrics = validation.metrics
    outcome.hubs = metrics.hub_count
    outcome.metrics = {
        "L": metrics.L,
        "X": metrics.X,
        "O": metrics.O,
        "B": metrics.B,
        "J": metrics.J,
    }
    outcome.status = "ok"
    log.info(
        "verified design: %d hubs, J=%.3f, proven minimum=%s",
        metrics.hub_count,
        metrics.J,
        summary.proven_minimum,
    )


def _try_picture(plan, design, validation, paths, outcome, log, config) -> None:
    try:
        write_plan_picture(plan, design, validation, paths["picture"], cfg=config)
        outcome.files["picture"] = paths["picture"]
    except OthelloError as problem:  # matplotlib missing: the picture is optional
        log.warning("no picture written: %s", problem)
        outcome.notes.append("picture skipped")


def _remove(path: Path, log) -> None:
    """Delete an old output file; if that is impossible, say so and carry on."""
    try:
        path.unlink(missing_ok=True)
    except OSError as problem:
        log.warning("could not remove old file %s: %s", path.name, problem)


def _record_failure(problem, plan, config, outcome, paths, log, stage, picture) -> None:
    report = failure_from_exception(
        problem,
        plan_id=outcome.plan_id,
        stage=None if isinstance(problem, OthelloError) else stage,
        settings_changed=non_default_settings(config),
    )
    outcome.failure = report
    outcome.status = (
        "no_design_found" if isinstance(problem, NoDesignFoundError) else "failed"
    )
    log_failure(log, report)
    # Never leave an old answer behind that looks current.
    for stale in ("official", "picture"):
        _remove(paths[stale], log)
        outcome.files.pop(stale, None)
    try:
        write_failure_json(report, paths["failure"])
        outcome.files["failure"] = paths["failure"]
    except OthelloError as write_problem:
        log.error("could not write the failure note: %s", write_problem)
    if plan is not None:  # the frontend can still draw the plan with no design on it
        try:
            write_overlay_json(
                plan,
                None,
                None,
                paths["overlay"],
                notes=[report.message],
                cfg=config,
                status="no_design_found",
            )
            outcome.files["overlay"] = paths["overlay"]
        except Exception as overlay_problem:  # noqa: BLE001  the failure note matters more than the drawing
            log.warning("no overlay written: %s", overlay_problem)


# ------------------------------------------------------------ many plans


def run_batch(
    source: str | Path,
    out_dir: str | Path,
    cfg: SolverConfig | None = None,
    summary_path: str | Path | None = None,
    **options: Any,
) -> list[RunOutcome]:
    """Run every plan in a folder (or the one file); one bad plan never stops the others."""
    outcomes = [
        run_plan(path, out_dir, cfg, **options) for path in find_plan_files(source)
    ]
    if summary_path is not None:
        write_summary(outcomes, summary_path)
    return outcomes


def summary_text(outcomes: Sequence[RunOutcome]) -> str:
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=SUMMARY_COLUMNS, lineterminator="\n")
    writer.writeheader()
    for outcome in outcomes:
        writer.writerow(outcome.summary_row())
    return buffer.getvalue()


def write_summary(outcomes: Sequence[RunOutcome], path: str | Path) -> Path:
    return atomic_write_text(path, summary_text(outcomes))


# ------------------------------------------------------------ command line


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m othello.run",
        description="Solve cable-ring plans and write the results.",
    )
    parser.add_argument("source", help="a plan file, or a folder of plan files")
    parser.add_argument(
        "--out", default="out", help="folder for the result files (default: out)"
    )
    parser.add_argument(
        "--summary", metavar="CSV", help="write a one-line-per-plan table (batch runs)"
    )
    parser.add_argument("--no-picture", action="store_true", help="skip the debug PNG")
    parser.add_argument(
        "--quiet", action="store_true", help="do not print progress to the screen"
    )
    add_config_arguments(parser)
    return parser


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    """Exit code 0 when the plans were processed (even with no design); 2 for a bad command, 1 when results cannot be written."""
    args = build_parser().parse_args(argv)
    try:
        cfg = config_from_args(args, env)
        files = find_plan_files(args.source)
    except (ConfigError, FileNotFoundError) as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 2
    try:
        ensure_out_dir(args.out)
    except OutputWriteError as problem:
        print(f"error: {problem}", file=sys.stderr)
        return 1
    outcomes = []
    for path in files:
        outcome = run_plan(
            path, args.out, cfg, console=False, picture=not args.no_picture
        )
        outcomes.append(outcome)
        if not args.quiet:
            print(_line(outcome))
    if args.summary:
        write_summary(outcomes, args.summary)
    return 0


def _line(outcome: RunOutcome) -> str:
    if outcome.status == "ok":
        return f"{outcome.plan_id}: ok  hubs={outcome.hubs} J={outcome.metrics['J']:.2f}  ({outcome.runtime_s:.1f}s)"
    reason = outcome.failure.message if outcome.failure else ""
    return f"{outcome.plan_id}: {outcome.status}  {reason}  ({outcome.runtime_s:.1f}s)"


if __name__ == "__main__":
    raise SystemExit(main())
