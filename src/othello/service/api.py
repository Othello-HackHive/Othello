"""The web API, written without any web framework: a request goes in, a response comes out.

    GET    /health                          is it up?
    POST   /api/jobs                        upload a plan (+ optional settings) and start a job   -> 202
    GET    /api/jobs/{id}                   status, summary, links to the result files
    GET    /api/jobs/{id}/events?after=N    progress steps since step N (poll this for the live view)
    GET    /api/jobs/{id}/result/{kind}     one result file: overlay | official | failure | picture | log | settings
    POST   /api/jobs/{id}/cancel            stop a queued or running job
    DELETE /api/jobs/{id}                   forget a finished job and its files

The same ``Api`` object is used by the local web server and by the AWS Lambda handler.

The body of ``POST /api/jobs`` is the plan JSON itself (ResPlan field names; shapes as GeoJSON or WKT),
or ``{"plan": {...plan...}, "settings": {"time_budget_s": 30}}`` when settings are sent too.  Every
error answer has the shape ``{"error": {"code", "message", "hint"}}``.
"""

from __future__ import annotations

import hmac
import json
import logging
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qs, urlsplit

from othello.io.errors import OthelloError
from othello.service.config import ServiceConfig, build_job_config
from othello.service.launcher import Launcher, LaunchError
from othello.service.plan_json import parse_plan_json, record_from_json
from othello.service.store import (
    ACTIVE,
    FILE_KINDS,
    PUBLIC_KINDS,
    Job,
    JobStore,
    new_job_id,
    valid_job_id,
)

log = logging.getLogger("othello.service.api")
VERSION = "1"
MAX_EVENTS_PER_REPLY = 200
ALLOWED_HEADERS = "content-type, x-api-key"
SECURITY_HEADERS = {
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
}
JOB_PATH = re.compile(r"^/api/jobs/([^/]+)(?:/(events|cancel|result)(?:/([^/]+))?)?$")


@dataclass
class Request:
    method: str
    path: str
    headers: Mapping[str, str] = field(default_factory=dict)
    query: Mapping[str, str] = field(default_factory=dict)
    body: bytes = b""

    def header(self, name: str) -> str | None:
        wanted = name.lower()
        for key, value in self.headers.items():
            if key.lower() == wanted:
                return value
        return None

    @classmethod
    def from_url(
        cls,
        method: str,
        url: str,
        headers: Mapping[str, str] | None = None,
        body: bytes = b"",
    ) -> Request:
        parts = urlsplit(url)
        query = {key: values[-1] for key, values in parse_qs(parts.query).items()}
        return cls(method.upper(), parts.path, headers or {}, query, body)


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    headers: dict[str, str] = field(default_factory=dict)

    def json(self) -> Any:
        return json.loads(self.body.decode("utf-8"))


class ApiError(Exception):
    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        hint: str = "",
        headers: dict[str, str] | None = None,
    ):
        super().__init__(message)
        self.status, self.code, self.message, self.hint = status, code, message, hint
        self.headers = headers or {}


def _json_response(
    status: int, data: Any, headers: dict[str, str] | None = None
) -> Response:
    return Response(
        status,
        json.dumps(data, separators=(",", ":")).encode("utf-8"),
        headers=headers or {},
    )


class Api:
    def __init__(
        self,
        store: JobStore,
        launcher: Launcher,
        limits: ServiceConfig | None = None,
        clock: Any = time.time,
    ):
        self.store, self.launcher, self.limits, self.clock = (
            store,
            launcher,
            limits or ServiceConfig(),
            clock,
        )

    # ---- entry point

    def handle(self, request: Request) -> Response:
        try:
            response = self._route(request)
        except ApiError as problem:
            response = _json_response(
                problem.status,
                {
                    "error": {
                        "code": problem.code,
                        "message": problem.message,
                        "hint": problem.hint,
                    }
                },
                problem.headers,
            )
        except (
            OthelloError
        ) as problem:  # an expected, explainable problem with what was sent
            response = _json_response(
                400,
                {
                    "error": {
                        "code": problem.code,
                        "message": str(problem),
                        "hint": problem.hint,
                    }
                },
            )
        except Exception:
            log.exception("unhandled error on %s %s", request.method, request.path)
            response = _json_response(
                500,
                {
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "something went wrong on the server",
                        "hint": "",
                    }
                },
            )
        return self._decorate(request, response)

    def too_large(self, request: Request) -> Response:
        """The answer for an upload that is over the size limit (a server can send it without reading the body)."""
        problem = ApiError(
            413, "TOO_LARGE", f"the upload is over {self.limits.max_upload_bytes} bytes"
        )
        body = {
            "error": {
                "code": problem.code,
                "message": problem.message,
                "hint": "send a smaller plan file",
            }
        }
        return self._decorate(request, _json_response(413, body))

    def _decorate(self, request: Request, response: Response) -> Response:
        headers = {**SECURITY_HEADERS, **response.headers}
        origin = request.header("origin")
        if origin and origin in self.limits.allowed_origins:
            headers["Access-Control-Allow-Origin"] = origin
            headers["Vary"] = "Origin"
        headers["Content-Type"] = response.content_type
        response.headers = headers
        return response

    # ---- routing

    def _route(self, request: Request) -> Response:
        path = request.path.rstrip("/") or "/"
        method = request.method.upper()
        if method == "OPTIONS":
            return self._preflight(request)
        if path == "/health":
            self._only(method, "GET")
            return _json_response(200, {"status": "ok", "version": VERSION})
        if path == "/api/jobs":
            self._authorise(request)
            self._only(method, "POST")
            return self._create(request)
        match = JOB_PATH.match(path)
        if match is None:
            raise ApiError(404, "NOT_FOUND", "no such address")
        self._authorise(request)
        job_id, section, kind = match.groups()
        if section is None:
            self._only(method, "GET", "DELETE")
            return self._show(job_id) if method == "GET" else self._forget(job_id)
        if section == "events":
            self._only(method, "GET")
            return self._events(job_id, request.query)
        if section == "cancel":
            self._only(method, "POST")
            return self._cancel(job_id)
        self._only(method, "GET")
        if kind is None:
            raise ApiError(
                404, "NOT_FOUND", f"name a result file: {', '.join(PUBLIC_KINDS)}"
            )
        return self._file(job_id, kind)

    @staticmethod
    def _only(method: str, *allowed: str) -> None:
        if method not in allowed:
            raise ApiError(
                405,
                "METHOD_NOT_ALLOWED",
                f"use {' or '.join(allowed)} here",
                headers={"Allow": ", ".join(allowed)},
            )

    def _preflight(self, request: Request) -> Response:
        origin = request.header("origin")
        if not origin or origin not in self.limits.allowed_origins:
            return Response(403, b"", "text/plain")
        return Response(
            204,
            b"",
            "text/plain",
            {
                "Access-Control-Allow-Methods": "GET, POST, DELETE, OPTIONS",
                "Access-Control-Allow-Headers": ALLOWED_HEADERS,
                "Access-Control-Max-Age": "600",
            },
        )

    def _authorise(self, request: Request) -> None:
        wanted = self.limits.api_key
        if wanted is None:
            return
        given = request.header("x-api-key") or ""
        if not hmac.compare_digest(given.encode("utf-8"), wanted.encode("utf-8")):
            raise ApiError(401, "UNAUTHORIZED", "a valid x-api-key header is required")

    # ---- the jobs

    def _create(self, request: Request) -> Response:
        if len(request.body) > self.limits.max_upload_bytes:
            raise ApiError(
                413,
                "TOO_LARGE",
                f"the upload is over {self.limits.max_upload_bytes} bytes",
                "send a smaller plan file",
            )
        content_type = (
            (request.header("content-type") or "").split(";")[0].strip().lower()
        )
        if content_type != "application/json":
            raise ApiError(
                415, "UNSUPPORTED_MEDIA_TYPE", "send the plan as application/json"
            )
        body = parse_plan_json(request.body, self.limits.max_upload_bytes)
        plan, settings = self._split_body(body)
        record = record_from_json(
            plan
        )  # checks the whole upload now, before anything is queued
        _, cleaned = build_job_config(settings, self.limits)
        if self.store.count_active() >= self.limits.max_active_jobs:
            raise ApiError(
                429,
                "BUSY",
                "too many jobs are running right now",
                "try again in a minute",
                {"Retry-After": "30"},
            )
        job = Job(
            job_id=new_job_id(),
            created_at=self.clock(),
            plan_id=record["id"],
            settings=cleaned,
            time_budget_s=cleaned["time_budget_s"],
        )
        self.store.create(job, json.dumps(plan, separators=(",", ":")).encode("utf-8"))
        try:
            self.launcher.launch(job.job_id)
        except LaunchError as problem:
            self.store.delete(
                job.job_id
            )  # nothing will ever run it, so do not leave it queued
            log.error("could not start job %s: %s", job.job_id, problem)
            raise ApiError(
                503,
                "SERVICE_UNAVAILABLE",
                "the solver could not be started",
                "try again in a moment",
            ) from problem
        return _json_response(
            202, {**self._view(job), "poll": f"/api/jobs/{job.job_id}/events?after=0"}
        )

    @staticmethod
    def _split_body(body: dict[str, Any]) -> tuple[dict[str, Any], Any]:
        if "plan" not in body:
            return body, None
        extra = sorted(set(body) - {"plan", "settings"})
        if extra:
            raise ApiError(
                400,
                "BAD_REQUEST",
                f"unexpected field(s) next to 'plan': {', '.join(extra)}",
            )
        if not isinstance(body["plan"], dict):
            raise ApiError(400, "BAD_REQUEST", "'plan' must be the plan object")
        settings = body.get("settings")
        if settings is not None and not isinstance(settings, dict):
            raise ApiError(400, "BAD_REQUEST", "'settings' must be an object")
        return body["plan"], settings

    def _job(self, job_id: str) -> Job:
        job = self.store.get(job_id) if valid_job_id(job_id) else None
        if job is None:
            raise ApiError(404, "NOT_FOUND", "no such job")
        return self._refresh(job)

    def _refresh(self, job: Job) -> Job:
        """A job whose worker vanished (crash, timeout) must not look busy for ever."""
        now = self.clock()
        stuck = (
            job.status == "running"
            and job.started_at is not None
            and now - job.started_at > job.time_budget_s + self.limits.stale_grace_s
        ) or (
            job.status == "queued"
            and now - job.created_at > self.limits.queue_timeout_s + job.time_budget_s
        )
        if stuck:
            job.status, job.finished_at = "failed", now
            job.error = {
                "code": "TIMED_OUT",
                "stage": "solve",
                "message": "the job did not finish in time",
                "hint": "try again, or use a longer time budget",
            }
            self.store.save(job)
        return job

    def _view(self, job: Job) -> dict[str, Any]:
        links = {
            kind: f"/api/jobs/{job.job_id}/result/{kind}"
            for kind in job.files
            if kind in PUBLIC_KINDS
        }
        return {
            "job_id": job.job_id,
            "status": job.status,
            "plan_id": job.plan_id,
            "created_at": job.created_at,
            "started_at": job.started_at,
            "finished_at": job.finished_at,
            "time_budget_s": job.time_budget_s,
            "settings": job.settings,
            "result": job.result,
            "error": job.error,
            "files": links,
        }

    def _show(self, job_id: str) -> Response:
        return _json_response(200, self._view(self._job(job_id)))

    def _events(self, job_id: str, query: Mapping[str, str]) -> Response:
        job = self._job(job_id)
        try:
            after = max(0, int(query.get("after", "0")))
        except ValueError as problem:
            raise ApiError(
                400, "BAD_REQUEST", "'after' must be a whole number"
            ) from problem
        events = self.store.events(job_id, after)[:MAX_EVENTS_PER_REPLY]
        return _json_response(
            200,
            {
                "status": job.status,
                "done": job.finished,
                "events": events,
                "next": after + len(events),
            },
        )

    def _file(self, job_id: str, kind: str) -> Response:
        job = self._job(job_id)
        if kind not in PUBLIC_KINDS:
            raise ApiError(
                404, "NOT_FOUND", f"result files are: {', '.join(PUBLIC_KINDS)}"
            )
        data = self.store.get_file(job.job_id, kind) if kind in job.files else None
        if data is None:
            raise ApiError(
                404,
                "NOT_FOUND",
                f"this job has no '{kind}' file (status: {job.status})",
            )
        return Response(200, data, FILE_KINDS[kind][1])

    def _cancel(self, job_id: str) -> Response:
        job = self._job(job_id)
        if job.finished:
            raise ApiError(
                409, "ALREADY_FINISHED", f"the job already ended ({job.status})"
            )
        self.launcher.cancel(job.job_id)
        return _json_response(200, self._view(self._job(job_id)))

    def _forget(self, job_id: str) -> Response:
        job = self._job(job_id)
        if job.status in ACTIVE:
            raise ApiError(409, "STILL_RUNNING", "cancel the job first")
        self.store.delete(job.job_id)
        return Response(204, b"", "text/plain")
