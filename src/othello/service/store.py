"""Jobs and where they are kept.

A *job* is one uploaded plan being solved.  Its life: ``queued`` -> ``running`` -> one of
``succeeded`` (a checked design was written), ``no_design`` (the solver found nothing valid),
``failed`` (an error, e.g. a bad upload) or ``cancelled``.

``JobStore`` is the small interface the rest of the service uses.  ``LocalJobStore`` keeps
everything in a folder (for development and tests); the AWS version (DynamoDB for the job record,
S3 for the files) implements the same methods.
"""

from __future__ import annotations

import json
import re
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, Field

from othello.io.atomic import atomic_write_bytes, atomic_write_text

JobStatus = Literal[
    "queued", "running", "succeeded", "no_design", "failed", "cancelled"
]
ACTIVE = ("queued", "running")
JOB_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")
MAX_EVENTS = 500

# result kind -> (stored file name, content type)
FILE_KINDS: dict[str, tuple[str, str]] = {
    "input": ("input.json", "application/json"),
    "official": ("official.json", "application/json"),
    "overlay": ("overlay.json", "application/json"),
    "failure": ("failure.json", "application/json"),
    "settings": ("settings.json", "application/json"),
    "picture": ("picture.png", "image/png"),
    "log": ("log.txt", "text/plain; charset=utf-8"),
}
PUBLIC_KINDS = ("official", "overlay", "failure", "picture", "log", "settings")


class Job(BaseModel):
    job_id: str
    status: JobStatus = "queued"
    created_at: float
    started_at: float | None = None
    finished_at: float | None = None
    plan_id: str | None = None
    settings: dict[str, Any] = Field(default_factory=dict)
    time_budget_s: float = 60.0
    result: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    files: list[str] = Field(default_factory=list)  # kinds that exist

    @property
    def finished(self) -> bool:
        return self.status not in ACTIVE


def new_job_id() -> str:
    return uuid.uuid4().hex


def valid_job_id(value: object) -> bool:
    return isinstance(value, str) and JOB_ID_PATTERN.fullmatch(value) is not None


class JobStore(Protocol):
    """What the service needs from a place that keeps jobs."""

    def create(self, job: Job, input_bytes: bytes) -> None: ...

    def get(self, job_id: str) -> Job | None: ...

    def save(self, job: Job) -> None: ...

    def append_event(self, job_id: str, event: dict[str, Any]) -> None: ...

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]: ...

    def request_cancel(self, job_id: str) -> None: ...

    def cancel_requested(self, job_id: str) -> bool: ...

    def put_file(self, job_id: str, kind: str, data: bytes) -> None: ...

    def get_file(self, job_id: str, kind: str) -> bytes | None: ...

    def count_active(self) -> int: ...

    def delete(self, job_id: str) -> None: ...

    def delete_older_than(self, seconds: float) -> int: ...


class LocalJobStore:
    """Jobs kept in a folder: ``<root>/<job_id>/{job.json, events.jsonl, cancel, files/...}``."""

    def __init__(self, root: str | Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    # ---- helpers

    def _dir(self, job_id: str) -> Path:
        if not valid_job_id(
            job_id
        ):  # never build a path from anything but a plain job id
            raise ValueError("invalid job id")
        return self.root / job_id

    # ---- the job record

    def create(self, job: Job, input_bytes: bytes) -> None:
        folder = self._dir(job.job_id)
        (folder / "files").mkdir(parents=True, exist_ok=False)
        atomic_write_bytes(folder / "files" / FILE_KINDS["input"][0], input_bytes)
        self.save(job)

    def get(self, job_id: str) -> Job | None:
        if not valid_job_id(job_id):
            return None
        path = self._dir(job_id) / "job.json"
        try:
            return Job.model_validate_json(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            return None

    def save(self, job: Job) -> None:
        atomic_write_text(self._dir(job.job_id) / "job.json", job.model_dump_json())

    # ---- progress events

    def append_event(self, job_id: str, event: dict[str, Any]) -> None:
        path = self._dir(job_id) / "events.jsonl"
        if len(self.events(job_id)) >= MAX_EVENTS:
            return
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(event, separators=(",", ":")) + "\n")

    def events(self, job_id: str, after: int = 0) -> list[dict[str, Any]]:
        path = self._dir(job_id) / "events.jsonl"
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return []
        found = []
        for line in lines[max(after, 0) :]:
            try:
                found.append(json.loads(line))
            except (
                ValueError
            ):  # a half-written last line; the next poll will see it whole
                break
        return found

    # ---- cancel flag

    def request_cancel(self, job_id: str) -> None:
        (self._dir(job_id) / "cancel").write_text("1", encoding="utf-8")

    def cancel_requested(self, job_id: str) -> bool:
        return (self._dir(job_id) / "cancel").exists()

    # ---- files

    def put_file(self, job_id: str, kind: str, data: bytes) -> None:
        name = FILE_KINDS[kind][0]
        atomic_write_bytes(self._dir(job_id) / "files" / name, data)

    def get_file(self, job_id: str, kind: str) -> bytes | None:
        if kind not in FILE_KINDS or not valid_job_id(job_id):
            return None
        try:
            return (self._dir(job_id) / "files" / FILE_KINDS[kind][0]).read_bytes()
        except FileNotFoundError:
            return None

    # ---- housekeeping

    def _all(self) -> list[Job]:
        jobs = []
        for folder in self.root.iterdir():
            if valid_job_id(folder.name):
                job = self.get(folder.name)
                if job is not None:
                    jobs.append(job)
        return jobs

    def count_active(self) -> int:
        return sum(1 for job in self._all() if job.status in ACTIVE)

    def delete(self, job_id: str) -> None:
        shutil.rmtree(self._dir(job_id), ignore_errors=True)

    def delete_older_than(self, seconds: float) -> int:
        cutoff = time.time() - seconds
        old = [
            job.job_id
            for job in self._all()
            if job.finished and (job.finished_at or job.created_at) < cutoff
        ]
        for job_id in old:
            self.delete(job_id)
        return len(old)
