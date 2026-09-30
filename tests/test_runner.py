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


# ── "no coverage by nature" is not "coverage failed" ────────────────────────
#
# One observation, an empty report, with two causes that must never be conflated:
# pytest emitting none means the run broke, bats emitting none means bats. The
# ecosystem declares which it is (`coverage_optional`); nothing else decides.


SHELL = ecosystem("shell")
DOTNET = ecosystem("dotnet")


def test_a_passing_bats_suite_with_no_kcov_is_a_pass_not_a_broken_run(tmp_path):
    """The failure this exists to prevent, in one line.

    `bats` writes no coverage and kcov is installed nowhere by default, so today's
    guard reported "BROKEN test run — tests failed or produced no coverage" and exited
    2 on a suite that passed. `mode: baseline` does not soften it: baseline suppresses
    the threshold, not a broken run.
    """
    outcome = run_one(SHELL, tmp_path, runner=lambda cmd, cwd: _completed(0), which=_which("bats"))

    assert outcome.ok
    assert outcome.unmeasured
    assert outcome.report is None
    assert outcome.error is None


def test_a_failing_bats_suite_is_still_broken(tmp_path):
    """Unmeasured is about coverage, never about the verdict.

    With no report to inspect, the exit status IS the whole result — so a non-zero one
    has to be the loudest thing in the outcome, not a footnote under a green gate.
    """
    outcome = run_one(SHELL, tmp_path, runner=lambda cmd, cwd: _completed(1), which=_which("bats"))

    assert not outcome.ok
    assert not outcome.unmeasured
    assert "the tests failed" in outcome.error
    assert "no coverage file found" not in outcome.error


def test_bats_missing_from_the_runner_is_still_nothing_ran(tmp_path):
    """127 is the one thing `coverage_optional` must not launder.

    "This ecosystem measures nothing" and "the test runner was never installed" both
    end with no report on disk, and treating the second as the first turns a suite that
    never executed into a green gate — the vacuous pass by a brand-new route.
    """
    outcome = run_one(SHELL, tmp_path, runner=lambda cmd, cwd: _completed(127), which=_which())

    assert not outcome.ok
    assert not outcome.unmeasured
    assert "command not found" in outcome.error


def test_a_dotnet_project_that_measures_nothing_stays_red(tmp_path):
    """Prlg.iSuite.iBeheer: a test project with no `coverlet.collector`.

    `dotnet test` passes and writes no `coverage.cobertura.xml`, which is a genuine
    "can measure, did not" — the case the shell fix must NOT sweep up with it. If this
    ever goes green the split has been implemented as a blanket softening.
    """
    outcome = run_one(DOTNET, tmp_path, runner=lambda cmd, cwd: _completed(0))

    assert not outcome.ok
    assert not outcome.unmeasured
    assert "no coverage file found" in outcome.error


def test_kcov_output_is_ingested_like_any_other_cobertura(tmp_path):
    """Present kcov means a measured shell half, not a permanently unmeasured one."""
    report = tmp_path / "coverage" / "kcov" / "kcov-merged" / "cobertura.xml"
    report.parent.mkdir(parents=True)
    report.write_text(
        "<coverage><packages><package><classes>"
        '<class filename="scripts/lib/scan.sh"><lines><line number="7" hits="1"/></lines></class>'
        "</classes></package></packages></coverage>"
    )

    outcome = run_one(
        SHELL, tmp_path, runner=lambda cmd, cwd: _completed(0), which=_which("bats", "kcov")
    )

    assert outcome.ok
    assert not outcome.unmeasured
    assert outcome.report.get("scripts/lib/scan.sh").is_covered(7)


def test_a_polyglot_repo_measures_the_half_that_can_and_names_the_half_that_cannot(tmp_path):
    """`dotnet,shell` in one repo: run both, merge, and do not go red for the shell half.

    The other half of the same trap — one unmeasurable ecosystem must not drag a
    perfectly good .NET number down with it, and the shell gap must not vanish either.
    """
    (tmp_path / "TestResults" / "guid").mkdir(parents=True)
    _write_cobertura(tmp_path / "TestResults" / "guid" / "coverage.cobertura.xml")

    result = run_tests(
        [DOTNET, SHELL], tmp_path, runner=lambda cmd, cwd: _completed(0), which=_which("bats")
    )

    assert not result.broken
    assert result.report.get("a.py") is not None
    assert [e.key for e in result.unmeasured] == ["shell"]


# ── a nested project runs where it lives ──────────────────────────────────────
#
# Detection's fallback finds `backend/` below a root with nothing in it. Its suite has
# to be installed, run and measured from `backend/`, and its report has to come back
# naming files the way the diff does.


def _nested(eco_key, project_dir):
    from dataclasses import replace

    from brimyr.detect import ecosystem

    return replace(ecosystem(eco_key), project_dir=project_dir, label=f"x in {project_dir}/")


def _cobertura_for(path, *filenames):
    classes = "".join(
        f'<class filename="{name}"><lines><line number="1" hits="1"/></lines></class>'
        for name in filenames
    )
    path.write_text(
        f"<coverage><packages><package><classes>{classes}</classes></package></packages></coverage>"
    )


def test_a_nested_project_runs_from_its_own_directory(tmp_path):
    backend = tmp_path / "backend"
    (backend / "app").mkdir(parents=True)
    (backend / "app" / "main.py").write_text("x = 1\n")
    _cobertura_for(backend / "coverage.xml", "app/main.py")
    seen = []

    outcome = run_one(
        _nested("python", "backend"),
        tmp_path,
        runner=lambda cmd, cwd: seen.append(cwd) or _completed(0),
        provision=False,
    )

    assert seen == [str(backend)]
    assert outcome.ok
    assert outcome.coverage_path == backend / "coverage.xml"
    # Repo-relative, like the diff: `backend/app/main.py`, not `app/main.py`.
    assert outcome.report.get("backend/app/main.py").is_covered(1)
    assert outcome.report.get("app/main.py") is None


def test_two_projects_with_the_same_file_name_stay_two_files(tmp_path):
    # Left project-relative, both would be `src/index.ts`, merged covered-wins, and the
    # tested project would answer for the untested one.
    for name in ("web", "admin"):
        (tmp_path / name / "src").mkdir(parents=True)
        (tmp_path / name / "src" / "index.ts").write_text("export {}\n")
    (tmp_path / "web" / "coverage").mkdir()
    (tmp_path / "web" / "coverage" / "lcov.info").write_text(
        "SF:src/index.ts\nDA:1,1\nend_of_record\n"
    )
    (tmp_path / "admin" / "coverage").mkdir()
    (tmp_path / "admin" / "coverage" / "lcov.info").write_text(
        "SF:src/index.ts\nDA:1,0\nend_of_record\n"
    )

    result = run_tests(
        [_nested("javascript", "web"), _nested("javascript", "admin")],
        tmp_path,
        runner=lambda cmd, cwd: _completed(0),
        provision=False,
    )

    assert result.report.get("web/src/index.ts").is_covered(1)
    assert not result.report.get("admin/src/index.ts").is_covered(1)


def test_a_path_that_is_not_under_the_project_is_left_as_written(tmp_path):
    # An absolute path, or one rooted at a `<source>` the tool chose, names no file
    # under backend/. Prefixing it would invent a path that matches nothing and drop
    # the file from the denominator; left alone, the suffix match still gets a chance.
    (tmp_path / "backend").mkdir()
    _cobertura_for(tmp_path / "backend" / "coverage.xml", "/abs/x.py", "pkg/mod.py")

    outcome = run_one(
        _nested("python", "backend"),
        tmp_path,
        runner=lambda cmd, cwd: _completed(0),
        provision=False,
    )

    assert {f.path for f in outcome.report.files} == {"/abs/x.py", "pkg/mod.py"}


def test_a_failing_suite_that_still_wrote_a_report_says_which_one_failed(tmp_path):
    _write_cobertura(tmp_path / "coverage.xml")
    result = run_tests([PY], tmp_path, command="pytest -q", runner=lambda c, w: _completed(1))
    assert result.broken
    assert result.failed == (PY,)
    assert result.outcomes[0].error == "the tests failed (`pytest -q` exited 1)."


# ── go: the module path comes off, and the source trims the blocks ────────────


GO = ecosystem("go")


def test_a_go_profile_is_mapped_through_go_mod_and_trimmed_by_the_source(tmp_path):
    (tmp_path / "go.mod").write_text('module "example.com/m" // the module\n\ngo 1.22\n')
    (tmp_path / "pkg").mkdir()
    (tmp_path / "pkg" / "f.go").write_text("package pkg\n\nfunc F() int {\n\treturn 1\n}\n")
    (tmp_path / "coverage.out").write_text("mode: set\nexample.com/m/pkg/f.go:3.14,5.2 1 1\n")

    outcome = run_one(GO, tmp_path, runner=lambda cmd, cwd: _completed(0), provision=False)

    assert outcome.ok
    # `pkg/f.go`, not the import path; line 4 only, not the `func` line or the brace.
    assert outcome.report.get("pkg/f.go").covered == {4}


def test_a_nested_go_module_claims_its_own_files(tmp_path):
    # Longest module path first: `example.com/m/tools` is not a package of `example.com/m`.
    (tmp_path / "go.mod").write_text("module example.com/m\n")
    (tmp_path / "tools").mkdir()
    (tmp_path / "tools" / "go.mod").write_text("module example.com/m/tools\n")
    profile = tmp_path / "all.out"
    profile.write_text(
        "mode: set\nexample.com/m/a.go:1.1,1.5 1 1\nexample.com/m/tools/b.go:1.1,1.5 1 0\n"
    )

    report = ingest_file(profile, CoverageFormat.GOCOVER, tmp_path)

    assert {f.path for f in report.files} == {"a.go", "tools/b.go"}


def test_a_go_profile_from_an_unknown_module_keeps_its_import_path(tmp_path):
    profile = tmp_path / "c.out"
    profile.write_text("mode: set\nother.org/x/y.go:1.1,1.5 1 1\n")
    assert ingest_file(profile, CoverageFormat.GOCOVER, tmp_path).get("other.org/x/y.go")


def test_a_file_that_is_not_a_go_profile_is_an_ingest_error(tmp_path):
    bad = tmp_path / "coverage.out"
    bad.write_text("not a profile\n")
    with pytest.raises(IngestError):
        ingest_file(bad, CoverageFormat.GOCOVER, tmp_path)
