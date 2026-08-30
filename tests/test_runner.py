"""Unit tests for the test runner + ingest (brimyr.runner)."""

from __future__ import annotations

import subprocess

import pytest

from brimyr.detect import CoverageFormat, ecosystem
from brimyr.runner import IngestError, ingest_file, run_one, run_tests

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


# ── the test-run timeout ──────────────────────────────────────────────────────


def test_a_hung_suite_is_a_broken_run_not_zero_percent():
    """Without a limit a hung suite holds the runner until the job timeout.

    On GitHub-hosted runners that is six hours, and the symptom (a job that never ends)
    points at everything except the coverage gate. The verdict must be a BROKEN run so
    it exits 2 and goes red, never 0% coverage.
    """
    import subprocess as sp  # nosec B404 - only to build a TimeoutExpired

    from brimyr.detect import ecosystem

    def hangs(command, cwd):
        raise sp.TimeoutExpired(cmd=command, timeout=3)

    outcome = run_one(ecosystem("python"), ".", runner=hangs)
    assert outcome.ok is False  # nosec B101
    assert outcome.returncode == 124  # nosec B101
    assert "did not finish" in (outcome.error or "")  # nosec B101
    assert "not 0% coverage" in (outcome.error or "")  # nosec B101
    assert "test_timeout" in (outcome.error or "")  # nosec B101 - names the way out


def test_an_injected_runner_still_takes_two_arguments():
    """`Runner` is a two-arg contract; the timeout binds to the default runner only.

    Widening it would break every injected runner in this suite at once.
    """
    import inspect

    from brimyr.runner import Runner  # noqa: F401

    seen: list[tuple[str, str]] = []

    def two_arg(command, cwd):
        seen.append((command, cwd))
        return subprocess.CompletedProcess(command, 0, "", "")

    from brimyr.detect import ecosystem

    run_one(ecosystem("python"), ".", runner=two_arg)
    assert len(seen) == 1  # nosec B101
    assert len(inspect.signature(two_arg).parameters) == 2  # nosec B101
