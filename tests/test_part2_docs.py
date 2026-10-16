"""T18: the docs for Part 2 must keep matching the code (a test fails if they drift)."""

import importlib
import re
from pathlib import Path

import pytest

from othello.io.errors import FAILURE_CODES, STAGES
from othello.models.result import VIOLATION_CODES
from othello.run import SUMMARY_COLUMNS, build_parser

DOCS = Path(__file__).resolve().parents[1] / "docs"


def doc(name):
    return (DOCS / name).read_text(encoding="utf-8")


def test_every_violation_code_is_listed_in_the_schema():
    text = doc("BACKEND_SCHEMA.md")
    assert [c for c in VIOLATION_CODES if f"`{c}`" not in text] == []


def test_every_failure_code_and_stage_is_listed_in_the_schema():
    text = doc("BACKEND_SCHEMA.md")
    assert [c for c in (*FAILURE_CODES, *STAGES) if f"`{c}`" not in text] == []


def test_every_runner_flag_is_described_in_the_app_flow():
    text = doc("APP_FLOW.md")
    flags = [
        flag
        for action in build_parser()._actions
        for flag in action.option_strings
        if flag.startswith("--") and flag != "--help"
    ]
    assert flags and [f for f in flags if f not in text] == []


def test_the_summary_columns_are_written_exactly_as_the_code_has_them():
    assert ", ".join(SUMMARY_COLUMNS) in doc("APP_FLOW.md")


@pytest.mark.parametrize(
    "dotted",
    [
        "othello.checker.check_design",
        "othello.checker.score_design",
        "othello.io.official.write_official_json",
        "othello.io.overlay.write_overlay_json",
        "othello.io.picture.write_plan_picture",
        "othello.run.run_plan",
        "othello.run.run_batch",
    ],
)
def test_every_part_two_function_named_in_the_schema_exists(dotted):
    module, name = dotted.rsplit(".", 1)
    assert hasattr(importlib.import_module(module), name)
    assert f"def {name}(" in doc("BACKEND_SCHEMA.md")


def test_the_schema_models_match_the_code_fields():
    from othello.models.result import (
        HubCountStatus,
        Metrics,
        RuleCheck,
        ValidationResult,
        Violation,
    )

    text = doc("BACKEND_SCHEMA.md")
    for model in (Violation, Metrics, RuleCheck, ValidationResult, HubCountStatus):
        block = text[text.index(f"class {model.__name__}(BaseModel)") :]
        block = block[: block.index("\nclass ") if "\nclass " in block[10:] else None]
        missing = [
            field
            for field in model.model_fields
            if not re.search(rf"\b{field}\b", block)
        ]
        assert missing == [], (model.__name__, missing)


def test_the_overlay_section_points_to_the_real_reference_files():
    text = doc("BACKEND_SCHEMA.md")
    assert "OVERLAY_FORMAT.md" in text and "tests/data/samples/" in text
    assert (DOCS / "OVERLAY_FORMAT.md").is_file()
    assert "OVERLAY_FORMAT.md" in doc("README.md") and "OVERLAY_FORMAT.md" in doc(
        "APP_FLOW.md"
    )


def test_every_relative_link_in_the_docs_points_at_a_file_that_exists():
    for name in ("README.md", "BACKEND_SCHEMA.md", "APP_FLOW.md", "OVERLAY_FORMAT.md"):
        for target in re.findall(r"\]\(([^)#]+?)(?:#[^)]*)?\)", doc(name)):
            if target.startswith(("http", "mailto")):
                continue
            assert (DOCS / target).resolve().exists(), (name, target)


# ------------------------------------------------------------- the web API doc


def api_doc():
    return doc("API.md")


def test_every_api_route_is_described():
    text = api_doc()
    for route in (
        "GET /health",
        "POST /api/jobs",
        "GET /api/jobs/{id}",
        "GET /api/jobs/{id}/events",
        "GET /api/jobs/{id}/result/{kind}",
        "POST /api/jobs/{id}/cancel",
        "DELETE /api/jobs/{id}",
    ):
        assert route in text, route


def test_the_api_doc_lists_the_real_progress_stages_statuses_and_result_kinds():
    from othello.run import PROGRESS_STAGES
    from othello.service.store import PUBLIC_KINDS, JobStatus

    text = api_doc()
    assert [s for s in PROGRESS_STAGES if f"`{s}`" not in text] == []
    assert [
        s for s in JobStatus.__args__ if f"`{s}`" not in text and s != "queued"
    ] == []
    assert (
        "`queued`" in text and [k for k in PUBLIC_KINDS if f"`{k}`" not in text] == []
    )


def test_the_api_doc_lists_the_real_settings_limits_and_environment_variables():
    from othello.service.config import ALLOWED_SETTINGS, ServiceConfig
    from othello.service.plan_json import SHAPE_KEYS

    text = api_doc()
    assert [s for s in ALLOWED_SETTINGS if f"`{s}`" not in text] == []
    assert [k for k in SHAPE_KEYS if f"`{k}`" not in text] == []
    limits = ServiceConfig()
    assert (
        f"from 1 to {limits.max_budget_s:g}" in text
        and f"default {limits.default_budget_s:g}" in text
    )
    for name in (
        "DEFAULT_BUDGET_S",
        "MAX_BUDGET_S",
        "MAX_UPLOAD_BYTES",
        "MAX_ACTIVE_JOBS",
        "RETENTION_S",
        "ORIGINS",
        "KEY",
    ):
        assert f"OTHELLO_API_{name}" in text, name


def test_every_error_code_the_api_can_send_is_in_the_doc():
    import re as regex

    source = (
        Path(__file__).resolve().parents[1] / "src" / "othello" / "service" / "api.py"
    ).read_text(encoding="utf-8")
    codes = set(regex.findall(r'ApiError\(\s*\d+,\s*"([A-Z_]+)"', source)) | {
        "INTERNAL_ERROR"
    }
    assert len(codes) >= 10 and [c for c in codes if f"`{c}`" not in api_doc()] == []
