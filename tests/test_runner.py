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

    outcome = run_one(ecosystem("python"), ".", runner=hangs, provision=False)
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

    run_one(ecosystem("python"), ".", runner=two_arg, provision=False)
    assert len(seen) == 1  # nosec B101
    assert len(inspect.signature(two_arg).parameters) == 2  # nosec B101


# ── provisioning: a detected suite has to be launchable ───────────────────────


def _which(*names):
    present = set(names)
    return lambda name: f"/usr/bin/{name}" if name in present else None


def test_a_uv_project_is_run_through_uv(tmp_path):
    """Detection found a suite; provisioning is what makes it runnable.

    The bug this closes: a fleet-provisioned workflow installs nothing, so bare
    `pytest` is not on PATH, and a repo with a perfectly good suite goes red.
    """
    _write_cobertura(tmp_path / "coverage.xml")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    seen = []

    outcome = run_one(
        PY,
        tmp_path,
        runner=lambda cmd, cwd: (seen.append(cmd), _completed(0))[1],
        which=_which("uv"),
    )

    assert seen == [f"uv run --with pytest-cov {PY.command_str()}"]
    assert outcome.ok
    assert outcome.provision_note


def test_an_explicit_test_command_is_never_wrapped(tmp_path):
    """The caller said how to run the tests; their setup is theirs to do."""
    _write_cobertura(tmp_path / "coverage.xml")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    seen = []

    run_one(
        PY,
        tmp_path,
        command="make cov",
        runner=lambda cmd, cwd: (seen.append(cmd), _completed(0))[1],
        which=_which("uv"),
    )

    assert seen == ["make cov"]


def test_provisioning_can_be_turned_off(tmp_path):
    _write_cobertura(tmp_path / "coverage.xml")
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    seen = []

    run_one(
        PY,
        tmp_path,
        provision=False,
        runner=lambda cmd, cwd: (seen.append(cmd), _completed(0))[1],
        which=_which("uv"),
    )

    assert seen == [PY.command_str()]


def test_setup_runs_before_the_tests(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "coverage").mkdir()
    (tmp_path / "coverage" / "lcov.info").write_text("SF:a.js\nDA:1,1\nend_of_record\n")
    seen = []

    outcome = run_one(
        ecosystem("javascript"),
        tmp_path,
        runner=lambda cmd, cwd: (seen.append(cmd), _completed(0))[1],
        which=_which("npm"),
    )

    assert seen[0].startswith("npm ci")
    assert seen[1] == ecosystem("javascript").command_str()
    assert outcome.ok


def test_a_failed_dependency_install_is_a_broken_run_and_says_so(tmp_path):
    """Not 0% and not a test failure: the tests never ran at all."""
    (tmp_path / "package.json").write_text("{}")
    seen = []

    def runner(cmd, cwd):
        seen.append(cmd)
        return _completed(1 if cmd.startswith("npm") else 0)

    outcome = run_one(ecosystem("javascript"), tmp_path, runner=runner, which=_which("npm"))

    assert len(seen) == 1, "the suite must not run against a half-installed environment"
    assert not outcome.ok
    assert "dependency install failed" in outcome.error
    assert "not run" in outcome.error


def test_command_not_found_blames_the_toolchain_not_the_coverage_config(tmp_path):
    """127 from the shell means nothing ran.

    The old message asked "did the test run emit coverage?", which sent everyone to
    look at a coverage config that was never the problem.
    """
    outcome = run_one(
        PY, tmp_path, runner=lambda cmd, cwd: _completed(127), which=_which("nothing")
    )

    assert not outcome.ok
    assert "command not found" in outcome.error
    assert "Nothing was measured" in outcome.error
    assert "no coverage file found" not in outcome.error


def test_a_setup_command_that_cannot_launch_is_a_broken_run(tmp_path):
    (tmp_path / "package.json").write_text("{}")

    def explodes(cmd, cwd):
        raise OSError("npm vanished")

    outcome = run_one(ecosystem("javascript"), tmp_path, runner=explodes, which=_which("npm"))

    assert not outcome.ok
    assert "npm vanished" in outcome.error
