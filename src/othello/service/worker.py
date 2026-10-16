"""The job worker: run one stored job and keep its progress and results in the store.

``execute_job`` is the whole job body.  A local launcher runs it in a child process; on AWS the same
function runs inside a Lambda invocation.  It never raises: whatever goes wrong becomes the job's
final status and error.
"""

from __future__ import annotations

import shutil
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from othello.io.errors import OthelloError
from othello.io.failure import failure_from_exception
from othello.models.config import SolverConfig
from othello.run import RunOutcome, run_plan
from othello.service.config import ServiceConfig, build_job_config
from othello.service.plan_json import plan_from_json
from othello.service.store import Job, JobStatus, JobStore

MAX_NOTES = 20
RESULT_FILE_KINDS = ("official", "overlay", "picture", "log", "failure", "settings")


def _summary(outcome: RunOutcome) -> dict[str, Any]:
    return {
        "status": outcome.status,
        "plan_id": outcome.plan_id,
        "hubs": outcome.hubs,
        "lower_bound": outcome.lower_bound,
        "proven_minimum": outcome.proven_min,
        "metrics": dict(outcome.metrics),
        "runtime_s": round(outcome.runtime_s, 2),
        "notes": outcome.notes[:MAX_NOTES],
    }


def _error(outcome: RunOutcome) -> dict[str, Any] | None:
    failure = outcome.failure
    if failure is None:
        return None
    return {
        "code": failure.code,
        "stage": failure.stage,
        "message": failure.message,
        "hint": failure.hint,
    }


def _load_from_store(
    store: JobStore, job_id: str
) -> Callable[[Any, SolverConfig], Any]:
    def loader(_: Any, cfg: SolverConfig) -> Any:
        data = store.get_file(job_id, "input")
        if data is None:
            raise OthelloError("the uploaded plan is missing from the job store")
        return plan_from_json(data, cfg)

    return loader


def execute_job(
    store: JobStore,
    job_id: str,
    limits: ServiceConfig | None = None,
    *,
    runner: Callable[..., RunOutcome] = run_plan,
    clock: Callable[[], float] = time.time,
) -> Job | None:
    """Run the job and leave it in a final state.  Returns the final job (None if it does not exist)."""
    limits = limits or ServiceConfig()
    job = store.get(job_id)
    if job is None:
        return None
    if job.finished:
        return job
    if store.cancel_requested(job_id):
        return _finish(store, job, "cancelled", clock)
    job.status, job.started_at = "running", clock()
    store.save(job)
    workdir = Path(tempfile.mkdtemp(prefix="othello_job_"))
    try:
        cfg, _ = build_job_config(job.settings, limits)
        outcome = runner(
            workdir / "input.json",
            workdir / "out",
            cfg,
            picture=True,
            loader=_load_from_store(store, job_id),
            progress=lambda event: store.append_event(job_id, event),
        )
        return _publish(store, job, outcome, clock)
    except Exception as problem:  # noqa: BLE001  a worker must always leave the job in a final state
        report = failure_from_exception(problem, plan_id=job.plan_id)
        job.error = {
            "code": report.code,
            "stage": report.stage,
            "message": report.message,
            "hint": report.hint,
        }
        return _finish(store, job, "failed", clock)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def _publish(
    store: JobStore, job: Job, outcome: RunOutcome, clock: Callable[[], float]
) -> Job:
    if store.cancel_requested(job.job_id):  # asked to stop while it ran: keep nothing
        return _finish(store, job, "cancelled", clock)
    job.plan_id = outcome.plan_id
    job.result = _summary(outcome)
    job.error = _error(outcome)
    for kind in RESULT_FILE_KINDS:
        path = outcome.files.get(kind)
        if path is not None and Path(path).is_file():
            store.put_file(job.job_id, kind, Path(path).read_bytes())
            job.files.append(kind)
    status: JobStatus = {"ok": "succeeded", "no_design_found": "no_design"}.get(
        outcome.status, "failed"
    )  # type: ignore[assignment]
    return _finish(store, job, status, clock)


def _finish(
    store: JobStore, job: Job, status: JobStatus, clock: Callable[[], float]
) -> Job:
    job.status = status
    job.finished_at = clock()
    store.save(job)
    return job
