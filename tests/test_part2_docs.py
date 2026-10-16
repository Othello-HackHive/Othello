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
