"""Unit tests for the test runner + ingest (brimyr.runner)."""

from __future__ import annotations

import subprocess

import pytest

from brimyr.detect import CoverageFormat, ecosystem
from brimyr.runner import IngestError, ingest_file, run_tests

PY = ecosystem("python")


def _completed(returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr="")


def _write_cobertura(path):
    path.write_text(
        "<coverage><packages><package><classes>"
        '<class filename="a.py"><lines><line number="1" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>"
    )


def test_clean_run_parses_coverage(tmp_path):
    _write_cobertura(tmp_path / "coverage.xml")
    result = run_tests([PY], tmp_path, runner=lambda cmd, cwd: _completed(0))
    assert not result.broken
    assert result.report.get("a.py").is_covered(1)
    outcome = result.outcomes[0]
    assert outcome.ok
    assert outcome.coverage_path.name == "coverage.xml"


def test_failed_tests_are_broken(tmp_path):
    _write_cobertura(tmp_path / "coverage.xml")
    result = run_tests([PY], tmp_path, runner=lambda cmd, cwd: _completed(1))
    assert result.broken
    assert not result.outcomes[0].ok


def test_missing_coverage_is_broken(tmp_path):
    result = run_tests([PY], tmp_path, runner=lambda cmd, cwd: _completed(0))
    assert result.broken
    assert "no coverage file" in result.outcomes[0].error


def test_command_override_used(tmp_path):
    _write_cobertura(tmp_path / "coverage.xml")
    seen = {}

    def runner(cmd, cwd):
        seen["cmd"] = cmd
        return _completed(0)

    run_tests([PY], tmp_path, command="make cov", runner=runner)
    assert seen["cmd"] == "make cov"


def test_ingest_missing_file_raises(tmp_path):
    with pytest.raises(IngestError):
        ingest_file(tmp_path / "nope.xml", CoverageFormat.COBERTURA)


def test_ingest_bad_xml_raises(tmp_path):
    bad = tmp_path / "c.xml"
    bad.write_text("<not-closed>")
    with pytest.raises(IngestError):
        ingest_file(bad, CoverageFormat.COBERTURA)


def test_run_one_enters_a_uv_projects_environment(tmp_path):
    """The detected command is resolved against the repo, not used verbatim.

    Hooked in the runner rather than in detection so a `--ecosystem`-forced run gets
    it too — that path bypasses detect_ecosystems() entirely.
    """
    from brimyr.detect import ecosystem
    from brimyr.runner import run_one

    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    seen: list[str] = []

    def fake(cmd: str, cwd: str) -> subprocess.CompletedProcess:
        seen.append(cmd)
        return subprocess.CompletedProcess([], 0)

    run_one(ecosystem("python"), tmp_path, runner=fake)
    assert seen == ["uv run pytest --cov --cov-report=xml --cov-report=term-missing"]


def test_an_explicit_command_still_wins_over_the_uv_prefix(tmp_path):
    from brimyr.detect import ecosystem
    from brimyr.runner import run_one

    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    seen: list[str] = []

    def fake(cmd: str, cwd: str) -> subprocess.CompletedProcess:
        seen.append(cmd)
        return subprocess.CompletedProcess([], 0)

    run_one(ecosystem("python"), tmp_path, command="make test", runner=fake)
    assert seen == ["make test"]
