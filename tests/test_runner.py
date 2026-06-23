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
