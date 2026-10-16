"""Starting jobs: a local launcher that runs each job in its own process.

The API only asks a launcher to ``launch`` or ``cancel`` a job by id.  On AWS the launcher starts a
Lambda invocation instead; this one is for a developer machine, a single server, and the tests.

Each job runs in a separate process (started fresh, not forked), so a crash or runaway job cannot take the
API down, at most ``max_concurrent`` jobs run at once (the rest wait their turn), and a job that
overruns its time budget plus a grace period is killed and marked failed.
"""

from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from collections import deque
from typing import Protocol

from othello.service.config import ServiceConfig
from othello.service.store import LocalJobStore
from othello.service.worker import execute_job

log = logging.getLogger("othello.service.launcher")
POLL_S = 0.25


class Launcher(Protocol):
    def launch(self, job_id: str) -> None: ...

    def cancel(self, job_id: str) -> None: ...


def _child(root: str, job_id: str, limits: ServiceConfig) -> None:
    execute_job(LocalJobStore(root), job_id, limits)


def fail_job(
    store: LocalJobStore, job_id: str, code: str, message: str, hint: str = ""
) -> None:
    """Put a job into the failed state from outside the worker (killed, crashed, never started)."""
    job = store.get(job_id)
    if job is None or job.finished:
        return
    job.status, job.finished_at = "failed", time.time()
    job.error = {"code": code, "stage": "solve", "message": message, "hint": hint}
    store.save(job)


class LocalProcessLauncher:
    def __init__(
        self,
        store: LocalJobStore,
        limits: ServiceConfig | None = None,
        max_concurrent: int = 2,
    ):
        self.store = store
        self.limits = limits or ServiceConfig()
        self.max_concurrent = max(1, max_concurrent)
        self._context = multiprocessing.get_context("spawn")
        self._lock = threading.Lock()
        self._waiting: deque[str] = deque()
        self._running: dict[str, tuple[multiprocessing.process.BaseProcess, float]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._watch, name="job-watcher", daemon=True
        )
        self._thread.start()

    # ---- the launcher interface

    def launch(self, job_id: str) -> None:
        with self._lock:
            self._waiting.append(job_id)
        self._fill_slots()

    def cancel(self, job_id: str) -> None:
        self.store.request_cancel(job_id)
        with self._lock:
            if job_id in self._waiting:
                self._waiting.remove(job_id)
            entry = self._running.pop(job_id, None)
        if entry is not None:
            entry[0].terminate()
            entry[0].join(timeout=5)
        job = self.store.get(job_id)
        if job is not None and not job.finished:
            job.status, job.finished_at = "cancelled", time.time()
            self.store.save(job)

    def shutdown(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
        with self._lock:
            for process, _ in self._running.values():
                process.terminate()
            self._running.clear()

    # ---- inside

    def _fill_slots(self) -> None:
        with self._lock:
            while self._waiting and len(self._running) < self.max_concurrent:
                job_id = self._waiting.popleft()
                process = self._context.Process(
                    target=_child,
                    args=(str(self.store.root), job_id, self.limits),
                    daemon=True,
                )
                process.start()
                self._running[job_id] = (process, time.monotonic())

    def _watch(self) -> None:
        while not self._stop.wait(POLL_S):
            try:
                self._check_running()
                self._fill_slots()
            except Exception:
                log.exception("job watcher problem")

    def _check_running(self) -> None:
        with self._lock:
            snapshot = list(self._running.items())
        for job_id, (process, started) in snapshot:
            job = self.store.get(job_id)
            budget = job.time_budget_s if job is not None else self.limits.max_budget_s
            overran = time.monotonic() - started > budget + self.limits.stale_grace_s
            if process.is_alive() and not overran:
                continue
            if process.is_alive():
                process.terminate()
                process.join(timeout=5)
                fail_job(
                    self.store,
                    job_id,
                    "TIMED_OUT",
                    "the job ran past its time limit and was stopped",
                )
            else:
                process.join(timeout=1)
                fail_job(
                    self.store,
                    job_id,
                    "INTERNAL_ERROR",
                    "the worker stopped before finishing the job",
                )
            with self._lock:
                self._running.pop(job_id, None)
