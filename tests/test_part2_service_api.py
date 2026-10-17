"""T21/T22: the web API (routes, limits, safety), the process launcher and the local web server."""

import json
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from fixtures.uploads import as_bytes, one_room_upload

from othello.service.api import Api, Request
from othello.service.config import ServiceConfig
from othello.service.launcher import LocalProcessLauncher
from othello.service.local_server import make_handler
from othello.service.store import Job, LocalJobStore, new_job_id
from othello.service.worker import execute_job

ORIGIN = "https://app.example.com"
JSON = {"Content-Type": "application/json"}


class HeldLauncher:
    """Accepts jobs but never runs them: jobs stay queued so the waiting states can be tested."""

    def __init__(self, store):
        self.store, self.launched, self.cancelled = store, [], []

    def launch(self, job_id):
        self.launched.append(job_id)

    def cancel(self, job_id):
        self.cancelled.append(job_id)
        self.store.request_cancel(job_id)
        job = self.store.get(job_id)
        job.status = "cancelled"
        self.store.save(job)


class InlineLauncher(HeldLauncher):
    """Runs each job right away in this process (the real chain, no extra process)."""

    def __init__(self, store, limits):
        super().__init__(store)
        self.limits = limits

    def launch(self, job_id):
        super().launch(job_id)
        execute_job(self.store, job_id, self.limits)


def make_api(tmp_path, launcher_class=HeldLauncher, **limits):
    config = ServiceConfig(allowed_origins=(ORIGIN,), **limits)
    store = LocalJobStore(tmp_path / "jobs")
    launcher = (
        launcher_class(store, config)
        if launcher_class is InlineLauncher
        else launcher_class(store)
    )
    return Api(store, launcher, config), store, launcher


def call(api, method, path, body=b"", headers=None):
    return api.handle(Request.from_url(method, path, headers or {}, body))


def post_plan(api, upload=None, headers=JSON, settings=None):
    body = one_room_upload() if upload is None else upload
    if settings is not None:
        body = {"plan": body, "settings": settings}
    return call(
        api,
        "POST",
        "/api/jobs",
        body if isinstance(body, bytes) else as_bytes(body),
        headers,
    )


@pytest.fixture
def held(tmp_path):
    return make_api(tmp_path)


# --------------------------------------------------- basics


def test_health_needs_no_key_and_says_ok(tmp_path):
    api, *_ = make_api(tmp_path, api_key="k")
    reply = call(api, "GET", "/health")
    assert reply.status == 200 and reply.json() == {"status": "ok", "version": "1"}


def test_unknown_addresses_and_wrong_methods_get_clear_answers(held):
    api, *_ = held
    assert call(api, "GET", "/nothing").status == 404
    assert call(api, "GET", "/api/jobs/" + "0" * 32 + "/result").status == 404
    reply = call(api, "GET", "/api/jobs")
    assert reply.status == 405 and reply.headers["Allow"] == "POST"
    assert call(api, "POST", "/health").status == 405
    assert call(api, "PUT", "/api/jobs/" + "0" * 32).status == 405


def test_every_answer_carries_the_safety_headers_and_error_shape(held):
    api, *_ = held
    reply = call(api, "GET", "/api/jobs/" + "0" * 32)
    assert reply.status == 404 and reply.headers["Cache-Control"] == "no-store"
    assert (
        reply.headers["X-Content-Type-Options"] == "nosniff"
        and reply.headers["Content-Type"] == "application/json"
    )
    assert set(reply.json()["error"]) == {"code", "message", "hint"}


def test_only_listed_websites_may_call_the_api_from_a_browser(held):
    api, *_ = held
    allowed = call(api, "GET", "/health", headers={"Origin": ORIGIN})
    other = call(api, "GET", "/health", headers={"Origin": "https://evil.example"})
    assert (
        allowed.headers["Access-Control-Allow-Origin"] == ORIGIN
        and allowed.headers["Vary"] == "Origin"
    )
    assert "Access-Control-Allow-Origin" not in other.headers
    assert "Access-Control-Allow-Origin" not in call(api, "GET", "/health").headers


def test_the_browser_preflight_is_answered_only_for_listed_websites(held):
    api, *_ = held
    ok = call(
        api,
        "OPTIONS",
        "/api/jobs",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert ok.status == 204 and "POST" in ok.headers["Access-Control-Allow-Methods"]
    assert (
        "x-api-key" in ok.headers["Access-Control-Allow-Headers"]
        and ok.headers["Access-Control-Allow-Origin"] == ORIGIN
    )
    assert (
        call(
            api, "OPTIONS", "/api/jobs", headers={"Origin": "https://evil.example"}
        ).status
        == 403
    )
    assert call(api, "OPTIONS", "/api/jobs").status == 403


def test_an_api_key_is_required_when_one_is_set(tmp_path):
    api, *_ = make_api(tmp_path, api_key="s3cret")
    assert post_plan(api).status == 401
    assert post_plan(api, headers={**JSON, "x-api-key": "wrong"}).status == 401
    assert call(api, "GET", "/api/jobs/" + "0" * 32).status == 401
    assert post_plan(api, headers={**JSON, "X-Api-Key": "s3cret"}).status == 202


# --------------------------------------------------- uploading


def test_a_good_upload_is_accepted_and_queued(held):
    api, store, launcher = held
    reply = post_plan(api)
    body = reply.json()
    assert (
        reply.status == 202 and body["status"] == "queued" and body["plan_id"] == "UP_1"
    )
    assert body[
        "poll"
    ] == f"/api/jobs/{body['job_id']}/events?after=0" and launcher.launched == [
        body["job_id"]
    ]
    assert store.get(body["job_id"]).settings == {"time_budget_s": 60.0}


def test_settings_can_travel_with_the_plan(held):
    api, _store, _ = held
    reply = post_plan(
        api, settings={"time_budget_s": 15, "cable_transit_policy": "forbid"}
    )
    assert reply.status == 202 and reply.json()["settings"] == {
        "time_budget_s": 15.0,
        "cable_transit_policy": "forbid",
    }
    assert reply.json()["time_budget_s"] == 15.0


@pytest.mark.parametrize(
    "body, headers, status, code",
    [
        (b"x" * 10, {"Content-Type": "text/plain"}, 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"x" * 10, {}, 415, "UNSUPPORTED_MEDIA_TYPE"),
        (b"not json", JSON, 400, "BAD_INPUT"),
        (b"[]", JSON, 400, "BAD_INPUT"),
        (b"{}", JSON, 400, "BAD_INPUT"),
        (as_bytes({"plan": {"area": 1}, "extra": 1}), JSON, 400, "BAD_REQUEST"),
        (as_bytes({"plan": "text"}), JSON, 400, "BAD_REQUEST"),
        (
            as_bytes({"plan": one_room_upload(), "settings": [1]}),
            JSON,
            400,
            "BAD_REQUEST",
        ),
        (
            as_bytes({"plan": one_room_upload(), "settings": {"time_budget_s": 99999}}),
            JSON,
            400,
            "BAD_SETTINGS",
        ),
        (
            as_bytes(
                {"plan": one_room_upload(), "settings": {"max_nodes_per_hub": 99}}
            ),
            JSON,
            400,
            "BAD_SETTINGS",
        ),
        (
            as_bytes(one_room_upload(wall="LINESTRING (0 0, 1 1)")),
            JSON,
            400,
            "BAD_INPUT",
        ),
    ],
)
def test_a_bad_upload_is_refused_at_once_and_nothing_is_queued(
    held, body, headers, status, code
):
    api, store, launcher = held
    reply = call(api, "POST", "/api/jobs", body, headers)
    assert reply.status == status and reply.json()["error"]["code"] == code
    assert launcher.launched == [] and store.count_active() == 0


def test_an_upload_over_the_size_limit_is_refused(tmp_path):
    api, _, launcher = make_api(tmp_path, max_upload_bytes=100)
    reply = post_plan(api)
    assert (
        reply.status == 413
        and reply.json()["error"]["code"] == "TOO_LARGE"
        and launcher.launched == []
    )
    assert api.too_large(Request("POST", "/api/jobs")).status == 413


def test_too_many_waiting_jobs_get_a_try_again_answer(tmp_path):
    api, store, _ = make_api(tmp_path, max_active_jobs=2)
    assert [post_plan(api).status for _ in range(2)] == [202, 202]
    third = post_plan(api)
    assert (
        third.status == 429
        and third.headers["Retry-After"] == "30"
        and store.count_active() == 2
    )


# --------------------------------------------------- looking at a job


def test_a_queued_job_shows_its_state_and_has_no_result_files_yet(held):
    api, *_ = held
    job_id = post_plan(api).json()["job_id"]
    view = call(api, "GET", f"/api/jobs/{job_id}").json()
    assert view["status"] == "queued" and view["result"] is None and view["files"] == {}
    missing = call(api, "GET", f"/api/jobs/{job_id}/result/overlay")
    assert (
        missing.status == 404 and "status: queued" in missing.json()["error"]["message"]
    )
    assert call(api, "GET", f"/api/jobs/{job_id}/result/secrets").status == 404
    assert (
        call(api, "GET", f"/api/jobs/{job_id}/result/input").status == 404
    )  # the upload itself is not served back
    assert call(api, "GET", f"/api/jobs/{job_id}/result").status == 404


@pytest.mark.parametrize(
    "bad_id",
    ["../../etc/passwd", "..", "%2e%2e", "A" * 32, "0" * 31, "0" * 33, "x" * 200],
)
def test_a_job_id_that_is_not_plain_hex_never_reaches_the_store(held, bad_id):
    api, *_ = held
    for suffix in ("", "/events", "/result/overlay"):
        assert call(api, "GET", f"/api/jobs/{bad_id}{suffix}").status == 404
    assert call(api, "DELETE", f"/api/jobs/{bad_id}").status == 404
    assert call(api, "POST", f"/api/jobs/{bad_id}/cancel").status == 404


def test_events_can_be_read_in_pages(held):
    api, store, _ = held
    job_id = post_plan(api).json()["job_id"]
    for n in range(5):
        store.append_event(job_id, {"stage": "x", "n": n})
    first = call(api, "GET", f"/api/jobs/{job_id}/events").json()
    assert first["next"] == 5 and first["done"] is False and first["status"] == "queued"
    later = call(api, "GET", f"/api/jobs/{job_id}/events?after=3").json()
    assert [e["n"] for e in later["events"]] == [3, 4] and later["next"] == 5
    assert call(api, "GET", f"/api/jobs/{job_id}/events?after=oops").status == 400
    assert call(api, "GET", f"/api/jobs/{job_id}/events?after=-4").json()["next"] == 5


def test_cancelling_a_queued_job_and_forgetting_a_finished_one(held):
    api, store, launcher = held
    job_id = post_plan(api).json()["job_id"]
    assert call(api, "DELETE", f"/api/jobs/{job_id}").status == 409  # still active
    cancelled = call(api, "POST", f"/api/jobs/{job_id}/cancel")
    assert (
        cancelled.status == 200
        and cancelled.json()["status"] == "cancelled"
        and launcher.cancelled == [job_id]
    )
    assert call(api, "POST", f"/api/jobs/{job_id}/cancel").status == 409  # already over
    assert call(api, "DELETE", f"/api/jobs/{job_id}").status == 204
    assert (
        call(api, "GET", f"/api/jobs/{job_id}").status == 404
        and store.get(job_id) is None
    )


def test_a_job_whose_worker_vanished_does_not_stay_busy_for_ever(tmp_path):
    now = [1000.0]
    config = ServiceConfig(
        allowed_origins=(ORIGIN,), stale_grace_s=60, queue_timeout_s=300
    )
    store = LocalJobStore(tmp_path / "jobs")
    api = Api(store, HeldLauncher(store), config, clock=lambda: now[0])
    running = Job(
        job_id=new_job_id(),
        created_at=1000,
        started_at=1000,
        status="running",
        time_budget_s=30,
    )
    waiting = Job(job_id=new_job_id(), created_at=1000, time_budget_s=30)
    store.create(running, b"{}")
    store.create(waiting, b"{}")
    now[0] = (
        1089  # past budget (30) + grace (60) for the running job; the queued one has 330 s in total
    )
    assert call(api, "GET", f"/api/jobs/{running.job_id}").json()["status"] == "running"
    now[0] = 1091
    view = call(api, "GET", f"/api/jobs/{running.job_id}").json()
    assert view["status"] == "failed" and view["error"]["code"] == "TIMED_OUT"
    assert call(api, "GET", f"/api/jobs/{waiting.job_id}").json()["status"] == "queued"
    now[0] = 1331
    assert (
        call(api, "GET", f"/api/jobs/{waiting.job_id}").json()["error"]["code"]
        == "TIMED_OUT"
    )
    assert store.count_active() == 0


def test_an_unexpected_server_problem_shows_no_internals(held, monkeypatch):
    api, store, _ = held

    def explode(job_id):
        raise RuntimeError("secret path C:/private/keys")

    monkeypatch.setattr(store, "get", explode)
    reply = call(api, "GET", "/api/jobs/" + "0" * 32)
    assert (
        reply.status == 500
        and "secret" not in reply.body.decode()
        and reply.json()["error"]["code"] == "INTERNAL_ERROR"
    )


# --------------------------------------------------- a whole job through the API


@pytest.fixture(scope="module")
def finished(tmp_path_factory):
    api, store, _ = make_api(tmp_path_factory.mktemp("done"), InlineLauncher)
    reply = post_plan(api, settings={"time_budget_s": 8})
    return api, store, reply.json()["job_id"]


def test_a_real_job_runs_through_the_api_and_every_file_can_be_downloaded(finished):
    api, _store, job_id = finished
    view = call(api, "GET", f"/api/jobs/{job_id}").json()
    assert (
        view["status"] == "succeeded"
        and view["result"]["hubs"] == 1
        and view["error"] is None
    )
    assert set(view["files"]) == {"official", "baseline", "overlay", "picture", "log", "settings"}
    overlay = call(api, "GET", view["files"]["overlay"])
    assert overlay.status == 200 and overlay.content_type == "application/json"
    assert overlay.json()["status"] == "ok" and overlay.json()["schema_version"] == 1
    assert call(api, "GET", view["files"]["official"]).json()["plan_id"] == "UP_1"
    assert call(api, "GET", view["files"]["baseline"]).json()["plan_id"] == "UP_1"
    picture = call(api, "GET", view["files"]["picture"])
    assert picture.content_type == "image/png" and picture.body[:4] == b"\x89PNG"
    assert call(api, "GET", view["files"]["log"]).content_type.startswith("text/plain")


def test_an_edited_candidate_is_checked_without_replacing_the_official_result(finished):
    api, _store, job_id = finished
    path = f"/api/jobs/{job_id}/result/official"
    official = call(api, "GET", path).json()
    checked = call(api, "POST", f"/api/jobs/{job_id}/validate", as_bytes(official), JSON)
    assert checked.status == 200 and checked.json()["valid"] is True
    assert checked.json()["metrics"]["J"] >= 0

    edited = json.loads(json.dumps(official))
    edited["hubs"][0]["location"]["x"] += 100
    rejected = call(api, "POST", f"/api/jobs/{job_id}/validate", as_bytes(edited), JSON)
    assert rejected.status == 200 and rejected.json()["valid"] is False
    assert rejected.json()["violations"]
    assert call(api, "GET", path).json() == official
    assert call(api, "POST", f"/api/jobs/{job_id}/validate", b"{}", JSON).status == 400


def test_moved_hub_is_rerouted_and_checked_on_the_api(finished):
    from shapely.geometry import Point

    from othello.service.config import build_job_config
    from othello.service.plan_json import plan_from_json

    api, store, job_id = finished
    path = f"/api/jobs/{job_id}/result/official"
    original = call(api, "GET", path).json()
    edited = json.loads(json.dumps(original))
    hub = edited["hubs"][0]
    cfg, _ = build_job_config(store.get(job_id).settings, api.limits)
    plan = plan_from_json(store.get_file(job_id, "input"), cfg)
    zone = plan.hub_zones_m[hub["room_id"]]
    x, y = hub["location"]["x"], hub["location"]["y"]
    move = next((point for point in ((x + 0.1, y), (x - 0.1, y), (x, y + 0.1), (x, y - 0.1))
                 if zone.covers(Point(point))), None)
    assert move is not None
    hub["location"] = {"x": move[0], "y": move[1]}
    reply = call(api, "POST", f"/api/jobs/{job_id}/reroute",
                 as_bytes({"hub_id": hub["hub_id"], "candidate": edited}), JSON)
    assert reply.status == 200, reply.json()
    result = reply.json()
    assert result["validation"]["valid"] is True
    assert result["validation"]["metrics"]["J"] >= 0
    assert result["official"]["hubs"][0]["location"]["x"] == pytest.approx(move[0], abs=0.0001)
    assert result["official"]["hubs"][0]["location"]["y"] == pytest.approx(move[1], abs=0.0001)
    assert result["official"]["hubs"][0]["route_segments"][0]["polyline"][0] == pytest.approx(move, abs=0.0001)
    assert call(api, "GET", path).json() == original

    hub["location"]["x"] += 100
    outside = call(api, "POST", f"/api/jobs/{job_id}/reroute",
                   as_bytes({"hub_id": hub["hub_id"], "candidate": edited}), JSON)
    assert outside.status == 400 and outside.json()["error"]["code"] == "BAD_REQUEST"


def test_three_room_upload_can_reroute_a_hub(tmp_path):
    from shapely.geometry import Point

    from othello.service.config import build_job_config
    from othello.service.plan_json import plan_from_json

    api, store, _ = make_api(tmp_path, InlineLauncher)
    upload = json.loads((Path(__file__).parent / "data/uploads/three_rooms.json").read_text())
    started = post_plan(api, upload=upload, settings={"time_budget_s": 10})
    job_id = started.json()["job_id"]
    assert call(api, "GET", f"/api/jobs/{job_id}").json()["status"] == "succeeded"
    original = call(api, "GET", f"/api/jobs/{job_id}/result/official").json()
    edited = json.loads(json.dumps(original))
    cfg, _ = build_job_config(store.get(job_id).settings, api.limits)
    plan = plan_from_json(store.get_file(job_id, "input"), cfg)
    hub = edited["hubs"][0]
    x, y = hub["location"]["x"], hub["location"]["y"]
    zone = plan.hub_zones_m[hub["room_id"]]
    move = next((point for point in ((x + 0.1, y), (x - 0.1, y), (x, y + 0.1), (x, y - 0.1))
                 if zone.covers(Point(point))), None)
    assert move is not None
    hub["location"] = {"x": move[0], "y": move[1]}
    reply = call(api, "POST", f"/api/jobs/{job_id}/reroute",
                 as_bytes({"hub_id": hub["hub_id"], "candidate": edited}), JSON)
    assert reply.status == 200, reply.json()
    assert reply.json()["validation"]["valid"]
    assert reply.json()["official"]["hubs"][1:] == original["hubs"][1:]


def test_the_progress_steps_of_a_real_job_can_be_followed_by_polling(finished):
    api, _, job_id = finished
    seen, position = [], 0
    for _ in range(10):
        page = call(api, "GET", f"/api/jobs/{job_id}/events?after={position}").json()
        seen += [e["stage"] for e in page["events"]]
        position = page["next"]
        if page["done"] and not page["events"]:
            break
    assert (
        seen[0] == "started"
        and seen[-1] == "done"
        and "searching" in seen
        and page["status"] == "succeeded"
    )


def test_a_bad_plan_that_passes_the_upload_check_still_ends_with_a_readable_failure(
    tmp_path,
):
    api, *_ = make_api(tmp_path, InlineLauncher)
    nothing_to_solve = one_room_upload(
        living=None
    )  # valid file, but no room to put cables in
    job_id = post_plan(api, nothing_to_solve, settings={"time_budget_s": 5}).json()[
        "job_id"
    ]
    view = call(api, "GET", f"/api/jobs/{job_id}").json()
    assert view["status"] in {"failed", "no_design"} and view["error"]["message"]
    assert (
        "failure" in view["files"]
        and call(api, "GET", view["files"]["failure"]).json()["status"] == "failed"
    )


# --------------------------------------------------- real processes


def wait_for(store, job_id, states, timeout=60):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        job = store.get(job_id)
        if job and job.status in states:
            return job
        time.sleep(0.1)
    raise AssertionError(f"job stayed {store.get(job_id).status}")


def drained(launcher, timeout=10):
    """True once the launcher has noticed every process has ended (it checks four times a second)."""
    end = time.monotonic() + timeout
    while launcher._running and time.monotonic() < end:
        time.sleep(0.05)
    return not launcher._running


def new_stored_job(store, upload=None, budget=8.0):
    job = Job(
        job_id=new_job_id(),
        created_at=time.time(),
        settings={"time_budget_s": budget},
        time_budget_s=budget,
    )
    store.create(job, as_bytes(upload or one_room_upload()))
    return job


def test_the_process_launcher_runs_waiting_jobs_one_after_another(tmp_path):
    store = LocalJobStore(tmp_path / "jobs")
    launcher = LocalProcessLauncher(store, ServiceConfig(), max_concurrent=1)
    try:
        first, second = (
            new_stored_job(store),
            new_stored_job(store, one_room_upload("UP_2")),
        )
        launcher.launch(first.job_id)
        launcher.launch(second.job_id)
        assert wait_for(store, first.job_id, {"succeeded"}).plan_id == "UP_1"
        assert wait_for(store, second.job_id, {"succeeded"}).plan_id == "UP_2"
        assert drained(launcher)
    finally:
        launcher.shutdown()


def test_a_job_waiting_in_line_can_be_cancelled_and_never_runs(tmp_path):
    store = LocalJobStore(tmp_path / "jobs")
    launcher = LocalProcessLauncher(store, ServiceConfig(), max_concurrent=1)
    try:
        first, second = new_stored_job(store), new_stored_job(store)
        launcher.launch(first.job_id)
        launcher.launch(second.job_id)
        launcher.cancel(second.job_id)
        assert (
            store.get(second.job_id).status == "cancelled"
            and store.events(second.job_id) == []
        )
        assert wait_for(store, first.job_id, {"succeeded"})
        assert store.get(second.job_id).status == "cancelled"
    finally:
        launcher.shutdown()


def test_a_job_that_runs_past_its_time_limit_is_killed_and_marked_failed(tmp_path):
    store = LocalJobStore(tmp_path / "jobs")
    launcher = LocalProcessLauncher(
        store, ServiceConfig(stale_grace_s=0), max_concurrent=1
    )
    try:
        job = new_stored_job(
            store
        )  # valid settings, so only the launcher's limit can stop it
        job.time_budget_s = 0.01
        store.save(job)
        launcher.launch(job.job_id)
        done = wait_for(store, job.job_id, {"failed", "succeeded"})
        assert done.status == "failed" and done.error["code"] == "TIMED_OUT"
        assert drained(launcher)
    finally:
        launcher.shutdown()


# --------------------------------------------------- the local web server


@pytest.fixture
def server(tmp_path):
    from http.server import ThreadingHTTPServer

    api, _store, _ = make_api(tmp_path, InlineLauncher, max_upload_bytes=200_000)
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), make_handler(api, api.limits.max_upload_bytes)
    )
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def http(method, url, body=None, headers=None):
    request = urllib.request.Request(
        url, data=body, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as reply:
            return reply.status, dict(reply.headers), reply.read()
    except urllib.error.HTTPError as problem:
        return problem.code, dict(problem.headers), problem.read()


def test_the_local_server_speaks_the_same_api_over_real_http(server):
    status, headers, body = http("GET", server + "/health", headers={"Origin": ORIGIN})
    assert (
        status == 200
        and json.loads(body)["status"] == "ok"
        and headers["Access-Control-Allow-Origin"] == ORIGIN
    )
    status, _, body = http(
        "POST",
        server + "/api/jobs",
        as_bytes({"plan": one_room_upload(), "settings": {"time_budget_s": 8}}),
        JSON,
    )
    job = json.loads(body)
    assert status == 202
    status, _, body = http("GET", server + f"/api/jobs/{job['job_id']}")
    assert status == 200 and json.loads(body)["status"] == "succeeded"
    status, headers, body = http(
        "GET", server + f"/api/jobs/{job['job_id']}/result/picture"
    )
    assert (
        status == 200
        and headers["Content-Type"] == "image/png"
        and body[:4] == b"\x89PNG"
    )
    assert http("GET", server + "/nope")[0] == 404


def test_the_local_server_refuses_a_huge_upload_without_reading_it(server):
    status, _, body = http("POST", server + "/api/jobs", b"x" * 300_000, JSON)
    assert status == 413 and json.loads(body)["error"]["code"] == "TOO_LARGE"
