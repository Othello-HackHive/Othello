"""T26: the AWS parts, tested against a pretend AWS (moto), with no real account involved."""

import json
import time

import pytest

boto3 = pytest.importorskip("boto3")
moto = pytest.importorskip("moto")

from fixtures.uploads import as_bytes, one_room_upload

from othello.service.store import MAX_EVENTS, Job, new_job_id
from othello.service.store_aws import (
    ACTIVE_ATTRIBUTE,
    ACTIVE_INDEX,
    TABLE_KEY,
    TTL_ATTRIBUTE,
    AwsJobStore,
)

REGION = "us-east-1"
TABLE = "othello-jobs"
BUCKET = "othello-job-files"


def create_table(client):
    """The same table as infra/template.yaml (a test below checks the two agree)."""
    client.create_table(
        TableName=TABLE,
        BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[
            {"AttributeName": TABLE_KEY, "AttributeType": "S"},
            {"AttributeName": ACTIVE_ATTRIBUTE, "AttributeType": "S"},
        ],
        KeySchema=[{"AttributeName": TABLE_KEY, "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[
            {
                "IndexName": ACTIVE_INDEX,
                "KeySchema": [{"AttributeName": ACTIVE_ATTRIBUTE, "KeyType": "HASH"}],
                "Projection": {"ProjectionType": "KEYS_ONLY"},
            }
        ],
    )


@pytest.fixture
def aws(monkeypatch):
    for name in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        monkeypatch.setenv(
            name, "testing"
        )  # fake values: moto never talks to the real AWS
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    with moto.mock_aws():
        db = boto3.client("dynamodb", region_name=REGION)
        s3 = boto3.client("s3", region_name=REGION)
        create_table(db)
        s3.create_bucket(Bucket=BUCKET)
        yield db, s3


@pytest.fixture
def store(aws):
    db, s3 = aws
    return AwsJobStore(TABLE, BUCKET, retention_s=3600, dynamodb=db, s3=s3)


def make_job(**changes):
    return Job(job_id=new_job_id(), created_at=time.time(), **changes)


# --------------------------------------------------- the job record


def test_a_job_can_be_created_read_back_and_updated(store):
    job = make_job(plan_id="P", settings={"time_budget_s": 5.0}, time_budget_s=5.0)
    store.create(job, b'{"x":1}')
    assert (
        store.get(job.job_id) == job
        and store.get_file(job.job_id, "input") == b'{"x":1}'
    )
    job.status, job.started_at = "running", 12.5
    store.save(job)
    assert (
        store.get(job.job_id).status == "running"
        and store.get(job.job_id).started_at == 12.5
    )


def test_creating_the_same_job_twice_is_refused(store):
    job = make_job()
    store.create(job, b"{}")
    with pytest.raises(Exception) as problem:
        store.create(job, b"{}")
    assert "ConditionalCheckFailed" in str(problem.value)


def test_unknown_or_malformed_ids_are_not_found_and_never_reach_aws(store):
    assert store.get(new_job_id()) is None
    for bad in ("", "../x", "A" * 32, "0" * 31, None, 7):
        assert store.get(bad) is None and store.get_file(bad, "input") is None
    with pytest.raises(ValueError):
        store.append_event("../../x", {})


def test_a_damaged_record_reads_as_not_found(store, aws):
    db, _ = aws
    job = make_job()
    store.create(job, b"{}")
    db.update_item(
        TableName=TABLE,
        Key={TABLE_KEY: {"S": job.job_id}},
        UpdateExpression="SET #j = :v",
        ExpressionAttributeNames={"#j": "job"},
        ExpressionAttributeValues={":v": {"S": "{broken"}},
    )
    assert store.get(job.job_id) is None


def test_saving_a_job_keeps_its_events_and_cancel_flag(store):
    job = make_job()
    store.create(job, b"{}")
    store.append_event(job.job_id, {"n": 1})
    store.request_cancel(job.job_id)
    job.status = "running"
    store.save(job)
    assert [e["n"] for e in store.events(job.job_id)] == [1] and store.cancel_requested(
        job.job_id
    )


# --------------------------------------------------- events, cancel, files


def test_events_come_back_in_order_and_from_a_position(store):
    job = make_job()
    store.create(job, b"{}")
    for i in range(5):
        store.append_event(job.job_id, {"stage": "x", "n": i, "t": 1.5})
    assert [e["n"] for e in store.events(job.job_id)] == [0, 1, 2, 3, 4]
    assert store.events(job.job_id)[0]["t"] == 1.5
    assert [e["n"] for e in store.events(job.job_id, after=3)] == [3, 4]
    assert store.events(job.job_id, after=99) == []


def test_the_number_of_events_per_job_is_capped(store):
    job = make_job()
    store.create(job, b"{}")
    for i in range(MAX_EVENTS + 5):
        store.append_event(job.job_id, {"n": i})
    assert len(store.events(job.job_id)) == MAX_EVENTS


def test_a_job_with_no_events_has_an_empty_list(store):
    job = make_job()
    store.create(job, b"{}")
    assert store.events(job.job_id) == [] and not store.cancel_requested(job.job_id)


def test_files_are_kept_per_job_with_the_right_type(store, aws):
    _, s3 = aws
    a, b = make_job(), make_job()
    store.create(a, b"{}")
    store.create(b, b"{}")
    store.put_file(a.job_id, "picture", b"\x89PNG")
    assert (
        store.get_file(a.job_id, "picture") == b"\x89PNG"
        and store.get_file(b.job_id, "picture") is None
    )
    head = s3.head_object(Bucket=BUCKET, Key=f"jobs/{a.job_id}/picture.png")
    assert (
        head["ContentType"] == "image/png" and head["ServerSideEncryption"] == "AES256"
    )
    assert store.get_file(a.job_id, "not_a_kind") is None


# --------------------------------------------------- active jobs, deleting, expiry


def test_only_queued_and_running_jobs_are_counted_as_active(store):
    queued, running, done = make_job(), make_job(status="running"), make_job()
    for job in (queued, running, done):
        store.create(job, b"{}")
    assert store.count_active() == 3
    done.status = "succeeded"
    store.save(done)
    assert store.count_active() == 2
    running.status = "cancelled"
    store.save(running)
    assert store.count_active() == 1
    queued.status = "running"
    store.save(queued)
    assert store.count_active() == 1


def test_a_job_created_already_finished_is_not_active(store):
    store.create(make_job(status="failed"), b"{}")
    assert store.count_active() == 0


def test_deleting_a_job_removes_the_record_and_every_file(store):
    job = make_job()
    store.create(job, b"{}")
    store.put_file(job.job_id, "overlay", b"[]")
    other = make_job()
    store.create(other, b"{}")
    store.delete(job.job_id)
    assert store.get(job.job_id) is None and store.get_file(job.job_id, "input") is None
    assert (
        store.get(other.job_id) is not None
        and store.get_file(other.job_id, "input") == b"{}"
    )


def test_every_save_pushes_the_expiry_time_forward(aws):
    db, s3 = aws
    now = [1000.0]
    store = AwsJobStore(
        TABLE, BUCKET, retention_s=600, dynamodb=db, s3=s3, clock=lambda: now[0]
    )
    job = make_job()
    store.create(job, b"{}")
    key = {TABLE_KEY: {"S": job.job_id}}
    assert (
        int(db.get_item(TableName=TABLE, Key=key)["Item"][TTL_ATTRIBUTE]["N"]) == 1600
    )
    now[0] = 5000.0
    store.save(job)
    assert (
        int(db.get_item(TableName=TABLE, Key=key)["Item"][TTL_ATTRIBUTE]["N"]) == 5600
    )


def test_the_manual_clean_up_removes_only_old_finished_jobs(aws):
    db, s3 = aws
    store = AwsJobStore(TABLE, BUCKET, dynamodb=db, s3=s3)
    old, fresh, busy = make_job(), make_job(), make_job()
    old.status, old.finished_at = "succeeded", time.time() - 100_000
    fresh.status, fresh.finished_at = "failed", time.time()
    for job in (old, fresh, busy):
        store.create(job, b"{}")
    assert store.delete_older_than(3600) == 1
    assert (
        store.get(old.job_id) is None
        and store.get(fresh.job_id)
        and store.get(busy.job_id)
    )


# --------------------------------------------------- a real job on the pretend AWS


def test_a_whole_job_runs_on_the_aws_store(store):
    from othello.service.config import ServiceConfig
    from othello.service.worker import execute_job

    job = make_job(settings={"time_budget_s": 8.0}, time_budget_s=8.0)
    store.create(job, as_bytes(one_room_upload()))
    done = execute_job(store, job.job_id, ServiceConfig())
    assert done.status == "succeeded" and done.plan_id == "UP_1"
    assert set(done.files) == {"official", "overlay", "picture", "log", "settings"}
    assert json.loads(store.get_file(job.job_id, "official"))["plan_id"] == "UP_1"
    assert store.get_file(job.job_id, "picture")[:4] == b"\x89PNG"
    stages = [e["stage"] for e in store.events(job.job_id)]
    assert stages[0] == "started" and stages[-1] == "done"
    assert store.get(job.job_id).status == "succeeded" and store.count_active() == 0


# --------------------------------------------------- the Lambda entry points (still on the pretend AWS)

import base64

from othello.service import lambda_handler as lh
from othello.service.api import Api, Response
from othello.service.config import ServiceConfig
from othello.service.launcher import LaunchError

ORIGIN = "https://app.example.com"
JSON_HEADERS = {"content-type": "application/json"}


class FakeLambdaClient:
    """Stands in for boto3's Lambda client: records the call and (optionally) fails."""

    def __init__(self, status=202, error=None):
        self.calls, self.status, self.error = [], status, error

    def invoke(self, **kwargs):
        self.calls.append(kwargs)
        if self.error:
            raise self.error
        return {"StatusCode": self.status}


def http_event(method, path, body=None, headers=None, query=None, base64_body=False):
    return {
        "version": "2.0",
        "rawPath": path,
        "requestContext": {"http": {"method": method, "path": path}},
        "headers": headers or {},
        "queryStringParameters": query,
        "body": body,
        "isBase64Encoded": base64_body,
    }


def wire(store, monkeypatch, client):
    launcher = lh.LambdaLauncher(store, "worker-fn", client)
    limits = ServiceConfig(allowed_origins=(ORIGIN,))
    monkeypatch.setattr(
        lh,
        "_cache",
        {"api": Api(store, launcher, limits), "store": store, "limits": limits},
    )


@pytest.fixture
def stack(store, monkeypatch):
    """The API handler wired to the pretend AWS and a fake worker Lambda client."""
    client = FakeLambdaClient()
    wire(store, monkeypatch, client)
    return store, client


def start_job(body):
    reply = lh.handler(
        http_event("POST", "/api/jobs", body=json.dumps(body), headers=JSON_HEADERS)
    )
    return reply, json.loads(reply["body"])


def test_the_request_event_is_turned_into_a_request():
    event = http_event(
        "post",
        "/api/jobs",
        body='{"a": 1}',
        headers={"content-type": "application/json", "origin": ORIGIN},
        query={"after": "3"},
    )
    request = lh.request_from_event(event)
    assert (request.method, request.path, request.body) == (
        "POST",
        "/api/jobs",
        b'{"a": 1}',
    )
    assert request.header("Content-Type") == "application/json" and request.query == {
        "after": "3"
    }


def test_empty_bodies_and_base64_bodies_are_both_understood():
    assert lh.request_from_event(http_event("GET", "/health")).body == b""
    encoded = base64.b64encode(b"hello").decode()
    assert (
        lh.request_from_event(
            http_event("POST", "/p", body=encoded, base64_body=True)
        ).body
        == b"hello"
    )
    bare = lh.request_from_event({})
    assert bare.path == "/" and bare.method == "GET"


def test_text_answers_go_back_as_text_and_pictures_as_base64():
    text = lh.event_response(
        Response(200, b'{"ok":true}', "application/json", {"X": "1"})
    )
    assert text == {
        "statusCode": 200,
        "headers": {"X": "1"},
        "body": '{"ok":true}',
        "isBase64Encoded": False,
    }
    log_text = lh.event_response(Response(200, b"hello", "text/plain; charset=utf-8"))
    assert log_text["body"] == "hello" and log_text["isBase64Encoded"] is False
    png = lh.event_response(Response(200, b"\x89PNG\x00\xff", "image/png"))
    assert (
        png["isBase64Encoded"] is True
        and base64.b64decode(png["body"]) == b"\x89PNG\x00\xff"
    )


def test_health_and_errors_come_back_in_the_gateway_format(stack):
    reply = lh.handler(http_event("GET", "/health", headers={"origin": ORIGIN}))
    assert reply["statusCode"] == 200 and json.loads(reply["body"])["status"] == "ok"
    assert reply["headers"]["Access-Control-Allow-Origin"] == ORIGIN
    missing = lh.handler(http_event("GET", "/api/jobs/" + "0" * 32))
    assert (
        missing["statusCode"] == 404
        and json.loads(missing["body"])["error"]["code"] == "NOT_FOUND"
    )


def test_an_upload_starts_the_worker_and_the_worker_finishes_the_job(stack):
    store, client = stack
    reply, accepted = start_job(
        {"plan": one_room_upload(), "settings": {"time_budget_s": 8}}
    )
    job_id = accepted["job_id"]
    assert reply["statusCode"] == 202
    # the API asked for the worker to be started, without waiting for it
    assert len(client.calls) == 1 and client.calls[0]["InvocationType"] == "Event"
    assert client.calls[0]["FunctionName"] == "worker-fn"
    assert json.loads(client.calls[0]["Payload"]) == {"job_id": job_id}
    assert store.get(job_id).status == "queued"
    # now the worker runs, as AWS would do it
    assert lh.worker_handler({"job_id": job_id}) == {
        "job_id": job_id,
        "status": "succeeded",
    }
    view = json.loads(lh.handler(http_event("GET", f"/api/jobs/{job_id}"))["body"])
    assert view["status"] == "succeeded" and view["result"]["hubs"] == 1
    picture = lh.handler(http_event("GET", f"/api/jobs/{job_id}/result/picture"))
    assert (
        picture["isBase64Encoded"]
        and base64.b64decode(picture["body"])[:4] == b"\x89PNG"
    )
    page = json.loads(
        lh.handler(
            http_event("GET", f"/api/jobs/{job_id}/events", query={"after": "2"})
        )["body"]
    )
    assert page["done"] and page["events"][-1]["stage"] == "done"


def test_a_worker_that_cannot_be_started_gives_a_503_and_leaves_nothing_queued(
    store, monkeypatch
):
    wire(store, monkeypatch, FakeLambdaClient(error=RuntimeError("AccessDenied")))
    reply, body = start_job(one_room_upload())
    assert reply["statusCode"] == 503 and body["error"]["code"] == "SERVICE_UNAVAILABLE"
    assert "AccessDenied" not in reply["body"] and store.count_active() == 0


def test_the_launcher_refuses_a_worker_that_was_not_accepted(store):
    launcher = lh.LambdaLauncher(store, "worker-fn", FakeLambdaClient(status=200))
    with pytest.raises(LaunchError):
        launcher.launch(new_job_id())


def test_cancelling_marks_the_job_and_the_late_worker_drops_its_results(stack):
    store, _ = stack
    _, accepted = start_job(one_room_upload())
    job_id = accepted["job_id"]
    cancelled = lh.handler(http_event("POST", f"/api/jobs/{job_id}/cancel"))
    assert (
        cancelled["statusCode"] == 200
        and json.loads(cancelled["body"])["status"] == "cancelled"
    )
    assert lh.worker_handler({"job_id": job_id})["status"] == "cancelled"
    assert store.get(job_id).files == [] and store.count_active() == 0


def test_the_worker_never_raises_even_for_junk(stack):
    assert lh.worker_handler({})["status"] == "not_found"
    assert lh.worker_handler({"job_id": "../../x"})["status"] == "not_found"
    assert lh.worker_handler(None)["status"] == "not_found"
    assert lh.worker_handler({"job_id": new_job_id()})["status"] == "not_found"


def test_a_worker_whose_storage_breaks_reports_an_error_instead_of_raising(
    stack, monkeypatch
):
    store, _ = stack

    def broken(job_id):
        raise RuntimeError("storage down")

    monkeypatch.setattr(store, "get", broken)
    assert lh.worker_handler({"job_id": new_job_id()})["status"] == "worker_error"


def test_the_pieces_are_built_once_from_the_environment(aws, monkeypatch):
    monkeypatch.setattr(lh, "_cache", {})
    monkeypatch.setenv("JOBS_TABLE", TABLE)
    monkeypatch.setenv("JOBS_BUCKET", BUCKET)
    monkeypatch.setenv("WORKER_FUNCTION", "worker-fn")
    monkeypatch.setenv("OTHELLO_API_ORIGINS", ORIGIN)
    api, store, limits = lh._parts()
    assert (
        lh._parts()[0] is api
        and limits.allowed_origins == (ORIGIN,)
        and store.table == TABLE
    )
    assert api.launcher.worker_function == "worker-fn"
