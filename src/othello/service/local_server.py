"""A small local web server for development: the same API, on your own machine.

    python -m othello.service.local_server --port 8000
    python -m othello.service.local_server --port 8000 --origin http://localhost:3000 --jobs-dir out/jobs

Then, for example:

    curl -X POST http://localhost:8000/api/jobs -H "Content-Type: application/json" --data-binary @plan.json
    curl http://localhost:8000/api/jobs/<job_id>/events?after=0
    curl http://localhost:8000/api/jobs/<job_id>/result/overlay

It is meant for trying things out (one machine, no encryption).  The real deployment runs the same
``Api`` behind AWS API Gateway and Lambda.
"""

from __future__ import annotations

import argparse
import logging
import os
from collections.abc import Sequence
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from othello.service.api import Api, Request
from othello.service.config import ServiceConfig
from othello.service.launcher import LocalProcessLauncher
from othello.service.store import LocalJobStore

DRAIN_LIMIT = 64 * 1024 * 1024


def make_handler(api: Api, max_body: int) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "othello"
        sys_version = ""

        def _discard(self, length: int) -> None:
            """Read and throw away an oversized body in small pieces, so the client sees the answer."""
            if length > DRAIN_LIMIT:
                self.close_connection = True
                return
            while length > 0:
                length -= len(self.rfile.read(min(length, 65536)) or b"\0" * length)

        def _serve(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > max_body:  # answer 413 without ever holding the body in memory
                self._discard(length)
                response = api.too_large(
                    Request.from_url(self.command, self.path, dict(self.headers))
                )
            else:
                body = self.rfile.read(length) if length else b""
                response = api.handle(
                    Request.from_url(self.command, self.path, dict(self.headers), body)
                )
            self.send_response(response.status)
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response.body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response.body)

        do_GET = do_POST = do_DELETE = do_OPTIONS = _serve

        def log_message(self, format: str, *args: object) -> None:
            logging.getLogger("othello.service.http").info(
                "%s %s", self.address_string(), format % args
            )

    return Handler


def build_api(jobs_dir: str | Path, limits: ServiceConfig, max_concurrent: int) -> Api:
    store = LocalJobStore(jobs_dir)
    return Api(store, LocalProcessLauncher(store, limits, max_concurrent), limits)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m othello.service.local_server",
        description=__doc__.split("\n\n")[0],
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="address to listen on (default 127.0.0.1: this machine only)",
    )
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--jobs-dir",
        default="out/jobs",
        help="where jobs and results are kept (default out/jobs)",
    )
    parser.add_argument(
        "--origin",
        action="append",
        default=[],
        help="a website allowed to call the API (repeatable)",
    )
    parser.add_argument(
        "--max-concurrent",
        type=int,
        default=2,
        help="jobs solved at the same time (default 2)",
    )
    args = parser.parse_args(argv)
    limits = ServiceConfig.from_env(os.environ)
    if args.origin:
        limits = ServiceConfig(
            **{**limits.__dict__, "allowed_origins": tuple(args.origin)}
        )
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
    )
    api = build_api(args.jobs_dir, limits, args.max_concurrent)
    server = ThreadingHTTPServer(
        (args.host, args.port), make_handler(api, limits.max_upload_bytes)
    )
    print(
        f"listening on http://{args.host}:{args.port}  (allowed websites: {', '.join(limits.allowed_origins)})"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        api.launcher.shutdown()  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
