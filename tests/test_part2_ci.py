"""T12: guard test, so the CI robot is not deleted or broken by accident.

Plain text checks only (no YAML library is needed to run the project).
"""

from pathlib import Path

WORKFLOW = Path(__file__).resolve().parents[1] / ".github" / "workflows" / "ci.yml"


def text() -> str:
    return WORKFLOW.read_text(encoding="utf-8")


def test_the_workflow_file_exists():
    assert WORKFLOW.is_file()


def test_it_runs_on_pushes_and_on_pull_requests_into_main():
    body = text()
    assert "\n  push:" in body
    assert "pull_request:" in body and "branches: [main]" in body


def test_it_has_no_path_filters_that_could_leave_a_required_check_pending():
    body = text()
    assert "paths:" not in body and "paths-ignore:" not in body


def test_it_lints_the_part_two_files_and_runs_every_test():
    body = text()
    for path in (
        "src/othello/checker",
        "src/othello/io",
        "tests/fixtures",
        "tests/mutations.py",
        "tests/test_part2_*.py",
    ):
        assert path in body
    assert "python -m pytest -q" in body


def test_the_whole_repo_lint_is_advisory_not_blocking():
    body = text()
    marker = "Lint (whole repo, advisory)"
    assert marker in body
    assert "continue-on-error: true" in body.split(marker, 1)[1].split("- name:", 1)[0]


def test_ruff_is_pinned_so_the_rules_do_not_drift():
    assert 'ruff==' in text()


def test_matplotlib_is_installed_so_the_picture_tests_really_run():
    assert "matplotlib==" in text()


def test_it_covers_the_oldest_supported_python_and_a_current_one():
    body = text()
    assert '"3.11"' in body and '"3.13"' in body


def test_it_installs_the_project_with_its_dev_tools():
    assert 'pip install -e ".[dev]"' in text()


def test_it_has_least_privilege_and_cancels_stale_runs():
    body = text()
    assert "contents: read" in body
    assert "cancel-in-progress: true" in body
