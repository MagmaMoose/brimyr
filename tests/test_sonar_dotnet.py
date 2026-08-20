"""Unit tests for the SonarScanner-for-.NET session (brimyr.sonar_dotnet).

The whole reason this module exists is ordering: `end` collects analysis data that the
compilation produced, so the scanner has to WRAP the build rather than follow it. Most
of what follows pins that ordering and the failure isolation around it.
"""

from __future__ import annotations

import subprocess  # nosec B404 - fixtures build fake CompletedProcess objects; nothing is executed

import pytest

from brimyr.sonar import SonarConfig
from brimyr.sonar_dotnet import build_begin_args, build_end_args, session

# A literal here would trip bandit B106 at each call site, and the suppression would
# have to ride on a 138-char line that `ruff format` is free to split — which moves the
# comment off the line the linter reports. A named constant keeps every call short.
_FAKE_TOKEN = "not-a-real-token"  # nosec B105 - test fixture

CONFIG = SonarConfig(host_url="https://sonar.example", token=_FAKE_TOKEN, project_key="owner_repo")
GLOBS = {"sonar.cs.cobertura.reportsPaths": ("**/TestResults/**/coverage.cobertura.xml",)}


def _recorder(returncodes=None):
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    codes = list(returncodes or [])

    def run(argv, cwd, env):
        calls.append((tuple(argv), dict(env)))
        code = codes.pop(0) if codes else 0
        return subprocess.CompletedProcess(argv, code, "", "boom" if code else "")

    return calls, run


class TestOrdering:
    def test_begin_runs_before_the_body_and_end_after(self):
        calls, run = _recorder()
        with session(CONFIG, ".", report_globs=GLOBS, runner=run, base_env={}) as outcome:
            calls.append((("<body>",), {}))
        verbs = [c[0][2] if len(c[0]) > 2 else c[0][0] for c in calls]
        assert verbs == ["begin", "<body>", "end"]  # nosec B101
        assert outcome.ok  # nosec B101

    def test_end_runs_even_when_the_body_raises(self):
        """A broken test run must still close the session — otherwise `.sonarqube`
        survives into the next build in the workspace and breaks it."""
        calls, run = _recorder()
        with (
            pytest.raises(RuntimeError),
            session(CONFIG, ".", report_globs=GLOBS, runner=run, base_env={}),
        ):
            raise RuntimeError("tests blew up")
        assert [c[0][2] for c in calls] == ["begin", "end"]  # nosec B101

    def test_body_exception_is_not_swallowed(self):
        """Failure isolation is one-way: the session never turns a red build green."""
        _, run = _recorder()
        with (
            pytest.raises(RuntimeError, match="tests blew up"),
            session(CONFIG, ".", report_globs=GLOBS, runner=run, base_env={}),
        ):
            raise RuntimeError("tests blew up")


class TestTheToken:
    def test_travels_in_the_environment_never_on_argv(self):
        calls, run = _recorder()
        with session(CONFIG, ".", report_globs=GLOBS, runner=run, base_env={}):
            pass
        for argv, env in calls:
            assert env["SONAR_TOKEN"] == _FAKE_TOKEN  # nosec B101
            assert not any(_FAKE_TOKEN in a for a in argv)  # nosec B101 - never a process listing


class TestBeginCarriesEverything:
    def test_coverage_glob_is_declared_at_begin(self):
        """`end` accepts only three flags, so the report path has to be on `begin` —
        as a wildcard, because the reports do not exist yet."""
        args = build_begin_args(CONFIG, report_globs=GLOBS)
        assert (  # nosec B101
            "/d:sonar.cs.cobertura.reportsPaths=**/TestResults/**/coverage.cobertura.xml" in args
        )

    def test_end_is_bare(self):
        assert build_end_args() == ["dotnet", "sonarscanner", "end"]  # nosec B101

    def test_cli_style_properties_are_translated(self):
        """A consumer should not have to know which scanner their repo happens to use."""
        cfg = SonarConfig(
            host_url="h", token=_FAKE_TOKEN, extra_args=("-Dsonar.projectVersion=1.2.3",)
        )
        assert "/d:sonar.projectVersion=1.2.3" in build_begin_args(cfg)  # nosec B101


class TestFailureIsolation:
    def test_no_url_skips_without_running_anything(self):
        calls, run = _recorder()
        cfg = SonarConfig(host_url="", token=_FAKE_TOKEN)
        with session(cfg, ".", runner=run, base_env={}) as out:
            pass
        assert calls == []  # nosec B101
        assert out.skipped == "no SonarQube host URL set"  # nosec B101

    def test_no_token_skips_without_running_anything(self):
        calls, run = _recorder()
        cfg = SonarConfig(host_url="h", token="")  # nosec B106 - the empty token IS the case
        with session(cfg, ".", runner=run, base_env={}) as out:
            pass
        assert calls == []  # nosec B101
        assert "no SonarQube token" in out.skipped  # nosec B101

    def test_a_failed_begin_does_not_run_end(self):
        """Nothing was hooked into the build, so there is nothing to collect."""
        calls, run = _recorder(returncodes=[1])
        with session(CONFIG, ".", report_globs=GLOBS, runner=run, base_env={}) as out:
            pass
        assert [c[0][2] for c in calls] == ["begin"]  # nosec B101
        assert out.ok is False  # nosec B101
        assert "non-blocking" in out.message  # nosec B101

    def test_a_missing_scanner_is_a_message_not_an_exception(self):
        def missing(argv, cwd, env):
            raise FileNotFoundError(argv[0])

        with session(CONFIG, ".", report_globs=GLOBS, runner=missing, base_env={}) as out:
            pass
        assert out.ok is False  # nosec B101
        assert "dotnet tool install" in out.message  # nosec B101

    def test_a_failed_end_cleans_the_work_directory(self, tmp_path):
        """`.sonarqube` holds the injected MSBuild hooks. Left behind, it breaks the
        NEXT build in the workspace for reasons that look unrelated."""
        (tmp_path / ".sonarqube").mkdir()
        _calls, run = _recorder(returncodes=[0, 1])
        with session(CONFIG, tmp_path, report_globs=GLOBS, runner=run, base_env={}) as out:
            pass
        assert out.cleaned is True  # nosec B101
        assert not (tmp_path / ".sonarqube").exists()  # nosec B101
