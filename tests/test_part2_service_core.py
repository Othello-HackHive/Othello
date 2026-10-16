"""T21/T22: the service core: settings limits, the job store, and the job worker."""

import json
import time

import pytest
from fixtures.uploads import as_bytes, one_room_upload

from othello.io.settings import ConfigError
from othello.run import RunOutcome
from othello.service.config import ServiceConfig, build_job_config
from othello.service.store import (
    MAX_EVENTS,
    Job,
    LocalJobStore,
    new_job_id,
    valid_job_id,
)
from othello.service.worker import execute_job

LIMITS = ServiceConfig()


# --------------------------------------------------- limits and settings


def test_the_defaults_are_safe_for_a_public_demo():
    assert (
        LIMITS.default_budget_s == 60
        and LIMITS.max_budget_s == 120
        and LIMITS.api_key is None
    )
    assert (
        LIMITS.max_upload_bytes == 5 * 1024 * 1024 and "*" not in LIMITS.allowed_origins
    )


def test_limits_can_be_changed_through_the_environment():
    config = ServiceConfig.from_env(
        {
            "OTHELLO_API_MAX_BUDGET_S": "200",
            "OTHELLO_API_DEFAULT_BUDGET_S": "30",
            "OTHELLO_API_MAX_ACTIVE_JOBS": "3",
            "OTHELLO_API_ORIGINS": "https://a.example, https://b.example",
            "OTHELLO_API_KEY": "secret",
        }
    )
    assert (config.max_budget_s, config.default_budget_s, config.max_active_jobs) == (
        200,
        30,
        3,
    )
    assert (
        config.allowed_origins == ("https://a.example", "https://b.example")
        and config.api_key == "secret"
    )


@pytest.mark.parametrize(
    "env",
    [
        {"OTHELLO_API_MAX_BUDGET_S": "soon"},
        {"OTHELLO_API_MAX_BUDGET_S": "-1"},
        {"OTHELLO_API_DEFAULT_BUDGET_S": "500"},
    ],
)
def test_bad_limits_in_the_environment_are_refused(env):
    with pytest.raises(ConfigError):
        ServiceConfig.from_env(env)


def test_no_settings_means_the_default_budget():
    cfg, cleaned = build_job_config(None, LIMITS)
    assert cfg.time_budget_s == 60 and cleaned == {"time_budget_s": 60.0}


def test_the_allowed_settings_are_accepted_and_applied():
    weights = {"length": 1, "crossing": 7, "overlap": 1, "bend": 0.5}
    cfg, cleaned = build_job_config(
        {"time_budget_s": 30, "cable_transit_policy": "forbid", "weights": weights},
        LIMITS,
    )
    assert (
        cfg.time_budget_s == 30
        and cfg.cable_transit_policy == "forbid"
        and cfg.weights.crossing == 7
    )
    assert cleaned["weights"]["crossing"] == 7.0


@pytest.mark.parametrize(
    "settings",
    [
        {"max_nodes_per_hub": 99},
        {"time_budget_s": 0},
        {"time_budget_s": 121},
        {"time_budget_s": "30"},
        {"time_budget_s": True},
        {"time_budget_s": float("nan")},
        {"cable_transit_policy": "teleport"},
        {"weights": {"length": 1}},
        {"weights": [1, 5, 1, 0.5]},
        {"weights": {"length": 1, "crossing": -1, "overlap": 1, "bend": 1}},
        {"weights": {"length": 1, "crossing": 1e9, "overlap": 1, "bend": 1}},
    ],
)
def test_a_setting_that_may_not_be_changed_or_is_out_of_range_is_refused(settings):
    with pytest.raises(ConfigError):
        build_job_config(settings, LIMITS)


# --------------------------------------------------- the job store


@pytest.fixture
def store(tmp_path):
    return LocalJobStore(tmp_path / "jobs")


def make_job(**changes):
    return Job(job_id=new_job_id(), created_at=time.time(), **changes)


def test_a_job_can_be_created_read_back_and_updated(store):
    job = make_job(plan_id="P", settings={"time_budget_s": 5.0}, time_budget_s=5.0)
    store.create(job, b"{}")
    assert store.get(job.job_id) == job and store.get_file(job.job_id, "input") == b"{}"
    job.status = "running"
    store.save(job)
    assert store.get(job.job_id).status == "running"


def test_job_ids_are_plain_hex_and_nothing_else_reaches_the_disk(store):
    assert valid_job_id(new_job_id())
    for bad in ("", "../x", "a" * 31, "A" * 32, "g" * 32, None, 5, "0" * 32 + "/x"):
        assert (
            not valid_job_id(bad)
            and store.get(bad) is None
            and store.get_file(bad, "input") is None
        )
    with pytest.raises(ValueError):
        store.append_event("../../x", {})


def test_a_missing_or_damaged_job_record_reads_as_not_found(store):
    assert store.get(new_job_id()) is None
    job = make_job()
    store.create(job, b"{}")
    (store.root / job.job_id / "job.json").write_text("{broken", encoding="utf-8")
    assert store.get(job.job_id) is None


def test_events_come_back_in_order_and_can_be_read_from_a_position(store):
    job = make_job()
    store.create(job, b"{}")
    for i in range(5):
        store.append_event(job.job_id, {"stage": "x", "n": i})
    assert [e["n"] for e in store.events(job.job_id)] == [0, 1, 2, 3, 4]
    assert [e["n"] for e in store.events(job.job_id, after=3)] == [3, 4]
    assert (
        store.events(job.job_id, after=99) == []
        and store.events(job.job_id, after=-5)[0]["n"] == 0
    )


def test_a_half_written_last_event_line_is_skipped_until_it_is_whole(store):
    job = make_job()
    store.create(job, b"{}")
    store.append_event(job.job_id, {"n": 1})
    with (store.root / job.job_id / "events.jsonl").open(
        "a", encoding="utf-8"
    ) as stream:
        stream.write('{"n": 2')
    assert [e["n"] for e in store.events(job.job_id)] == [1]


def test_the_number_of_events_per_job_is_capped(store):
    job = make_job()
    store.create(job, b"{}")
    for i in range(MAX_EVENTS + 20):
        store.append_event(job.job_id, {"n": i})
    assert len(store.events(job.job_id)) == MAX_EVENTS


def test_the_cancel_flag_and_files_work_per_job(store):
    a, b = make_job(), make_job()
    store.create(a, b"{}")
    store.create(b, b"{}")
    store.request_cancel(a.job_id)
    assert store.cancel_requested(a.job_id) and not store.cancel_requested(b.job_id)
    store.put_file(a.job_id, "overlay", b"[1]")
    assert (
        store.get_file(a.job_id, "overlay") == b"[1]"
        and store.get_file(b.job_id, "overlay") is None
    )
    assert store.get_file(a.job_id, "not_a_kind") is None


def test_active_jobs_are_counted_and_finished_old_ones_are_cleaned_up(store):
    running, done_old, done_new = make_job(status="running"), make_job(), make_job()
    done_old.status, done_old.finished_at = "succeeded", time.time() - 100_000
    done_new.status, done_new.finished_at = "failed", time.time()
    for job in (running, done_old, done_new):
        store.create(job, b"{}")
    assert store.count_active() == 1
    assert store.delete_older_than(3600) == 1
    assert (
        store.get(done_old.job_id) is None
        and store.get(running.job_id)
        and store.get(done_new.job_id)
    )
    store.delete(done_new.job_id)
    assert store.get(done_new.job_id) is None


# --------------------------------------------------- the worker


def queued(store, upload, **settings):
    job = make_job(settings=settings, time_budget_s=settings.get("time_budget_s", 60.0))
    store.create(job, upload if isinstance(upload, bytes) else as_bytes(upload))
    return job


def stub_runner(status="ok", **outcome_fields):
    def runner(source, out_dir, cfg, **kwargs):
        kwargs["progress"]({"stage": "started", "message": "go"})
        outcome = RunOutcome(plan_id="STUB", status=status, **outcome_fields)
        return outcome

    return runner


def test_a_real_upload_is_solved_and_every_result_is_kept(store):
    job = queued(store, one_room_upload(), time_budget_s=8)
    done = execute_job(store, job.job_id, LIMITS)
    assert done.status == "succeeded" and done.plan_id == "UP_1" and done.error is None
    assert set(done.files) == {"official", "overlay", "picture", "log", "settings"}
    assert (
        done.result["status"] == "ok"
        and done.result["hubs"] == 1
        and done.result["proven_minimum"] is True
    )
    assert done.started_at and done.finished_at >= done.started_at
    official = json.loads(store.get_file(job.job_id, "official"))
    assert (
        official["plan_id"] == "UP_1"
        and store.get_file(job.job_id, "picture")[:4] == b"\x89PNG"
    )
    stages = [e["stage"] for e in store.events(job.job_id)]
    assert stages[0] == "started" and stages[-1] == "done" and "searching" in stages
    assert store.get(job.job_id) == done


def test_an_unusable_upload_ends_as_failed_with_a_clear_failure_note(store):
    job = queued(store, b"this is not json", time_budget_s=5)
    done = execute_job(store, job.job_id, LIMITS)
    assert (
        done.status == "failed"
        and done.error["code"] == "BAD_INPUT"
        and done.error["stage"] == "load"
    )
    assert "not valid JSON" in done.error["message"] and "failure" in done.files
    assert json.loads(store.get_file(job.job_id, "failure"))["code"] == "BAD_INPUT"
    assert store.get_file(job.job_id, "official") is None


def test_a_plan_with_no_design_is_its_own_status_with_the_reason(store):
    job = queued(store, one_room_upload(area=64.0), time_budget_s=5)
    done = execute_job(store, job.job_id, LIMITS, runner=stub_runner("no_design_found"))
    assert (
        done.status == "no_design"
        and done.result["status"] == "no_design_found"
        and done.error is None
    )


def test_a_crash_inside_the_run_still_ends_the_job_cleanly(store):
    def exploding(*args, **kwargs):
        raise RuntimeError("boom")

    job = queued(store, one_room_upload())
    done = execute_job(store, job.job_id, LIMITS, runner=exploding)
    assert (
        done.status == "failed"
        and done.error["code"] == "INTERNAL_ERROR"
        and done.finished_at
    )


def test_settings_that_no_longer_pass_the_limits_fail_the_job_not_the_worker(store):
    job = queued(store, one_room_upload())
    job.settings = {"time_budget_s": 99999}
    store.save(job)
    done = execute_job(store, job.job_id, LIMITS, runner=stub_runner())
    assert done.status == "failed" and done.error["code"] == "BAD_SETTINGS"


def test_a_job_cancelled_before_it_starts_never_runs(store):
    job = queued(store, one_room_upload())
    store.request_cancel(job.job_id)

    def must_not_run(*args, **kwargs):
        raise AssertionError("ran a cancelled job")

    done = execute_job(store, job.job_id, LIMITS, runner=must_not_run)
    assert done.status == "cancelled" and done.files == []


def test_a_job_cancelled_while_running_keeps_no_results(store):
    job = queued(store, one_room_upload())

    def cancelled_midway(source, out_dir, cfg, **kwargs):
        store.request_cancel(job.job_id)
        return RunOutcome(plan_id="X", status="ok", hubs=1)

    done = execute_job(store, job.job_id, LIMITS, runner=cancelled_midway)
    assert done.status == "cancelled" and done.result is None and done.files == []


def test_a_finished_or_unknown_job_is_left_alone(store):
    assert execute_job(store, new_job_id(), LIMITS) is None
    job = queued(store, one_room_upload())
    job.status = "succeeded"
    store.save(job)
    assert (
        execute_job(store, job.job_id, LIMITS, runner=stub_runner()).status
        == "succeeded"
    )
    assert store.events(job.job_id) == []


def test_the_temporary_work_folder_is_always_removed(store, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
    job = queued(store, one_room_upload(), time_budget_s=8)
    execute_job(store, job.job_id, LIMITS, runner=stub_runner())
    assert not list(tmp_path.glob("othello_job_*"))
