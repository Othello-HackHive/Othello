"""AWS Lambda entry points: the web API and the job worker.

    handler         API Gateway (HTTP API) calls this for every web request -> ``Api.handle``
    worker_handler  started asynchronously by the API for one job            -> ``execute_job``

Both functions run from the same container image; the template picks the function with its command.
Names of the table, the bucket and the worker function come from environment variables set by the
template (see ``infra/template.yaml``).  Everything else (limits, allowed websites, API key) is read by
``ServiceConfig.from_env``.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import time
from typing import Any

from othello.service.api import Api, Request, Response
from othello.service.config import ServiceConfig
from othello.service.launcher import LaunchError
from othello.service.store import JobStore
from othello.service.worker import execute_job

log = logging.getLogger("othello.service.lambda")
log.setLevel(logging.INFO)

TEXT_TYPES = ("application/json", "text/")


class LambdaLauncher:
    """Starts a job by invoking the worker function asynchronously (the API does not wait for it)."""

    def __init__(self, store: JobStore, worker_function: str, client: Any = None):
        if client is None:
            import boto3

            client = boto3.client("lambda")
        self.store, self.worker_function, self.client = store, worker_function, client

    def launch(self, job_id: str) -> None:
        try:
            reply = self.client.invoke(
                FunctionName=self.worker_function,
                InvocationType="Event",
                Payload=json.dumps({"job_id": job_id}).encode("utf-8"),
            )
        except Exception as problem:
            raise LaunchError(
                f"could not start the worker: {type(problem).__name__}"
            ) from problem
        if reply.get("StatusCode") != 202:
            raise LaunchError(
                f"the worker was not accepted (status {reply.get('StatusCode')})"
            )

    def cancel(self, job_id: str) -> None:
        """A running worker cannot be stopped from outside; it sees the flag and drops its results."""
        self.store.request_cancel(job_id)
        job = self.store.get(job_id)
        if job is not None and not job.finished:
            job.status, job.finished_at = "cancelled", time.time()
            self.store.save(job)


# ---------------------------------------------------------------- building the pieces once per container

_cache: dict[str, Any] = {}


def _parts() -> tuple[Api, JobStore, ServiceConfig]:
    if not _cache:
        from othello.service.store_aws import AwsJobStore

        limits = ServiceConfig.from_env()
        store = AwsJobStore(
            os.environ["JOBS_TABLE"],
            os.environ["JOBS_BUCKET"],
            retention_s=limits.retention_s,
        )
        launcher = LambdaLauncher(store, os.environ["WORKER_FUNCTION"])
        _cache.update(api=Api(store, launcher, limits), store=store, limits=limits)
    return _cache["api"], _cache["store"], _cache["limits"]


# ---------------------------------------------------------------- the web API


def request_from_event(event: dict[str, Any]) -> Request:
    """An API Gateway HTTP API (payload format 2.0) event -> our Request."""
    http = event.get("requestContext", {}).get("http", {})
    body = event.get("body") or ""
    raw = (
        base64.b64decode(body) if event.get("isBase64Encoded") else body.encode("utf-8")
    )
    return Request(
        method=str(http.get("method", "GET")).upper(),
        path=event.get("rawPath") or http.get("path") or "/",
        headers={str(k): str(v) for k, v in (event.get("headers") or {}).items()},
        query={
            str(k): str(v)
            for k, v in (event.get("queryStringParameters") or {}).items()
        },
        body=raw,
    )


def event_response(response: Response) -> dict[str, Any]:
    """Our Response -> what API Gateway expects back (pictures travel base64-encoded)."""
    is_text = response.content_type.startswith(TEXT_TYPES)
    return {
        "statusCode": response.status,
        "headers": dict(response.headers),
        "body": response.body.decode("utf-8")
        if is_text
        else base64.b64encode(response.body).decode("ascii"),
        "isBase64Encoded": not is_text,
    }


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    api, _, _ = _parts()
    return event_response(api.handle(request_from_event(event)))


# ---------------------------------------------------------------- the worker


def worker_handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    """Run one job.  Never raises: a failing Lambda would be retried and could run a job twice."""
    job_id = str((event or {}).get("job_id", ""))
    try:
        _, store, limits = _parts()
        job = execute_job(store, job_id, limits)
        status = job.status if job is not None else "not_found"
    except Exception:
        log.exception("worker problem for job %s", job_id)
        status = "worker_error"
    log.info("job %s finished as %s", job_id, status)
    return {"job_id": job_id, "status": status}
