"""Unit tests for the sonar-scanner runner (brimyr.sonar)."""

from __future__ import annotations

import subprocess

from brimyr.sonar import SonarConfig, build_scanner_args, run_scanner


def _completed(returncode=0, stderr=""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout="", stderr=stderr)


def test_build_args_includes_props_but_not_token():
    config = SonarConfig(
        host_url="https://sonar.example.com",
        token="secret",
        project_key="my-proj",
        coverage_report_paths={"sonar.python.coverage.reportPaths": ("coverage.xml",)},
    )
    args = build_scanner_args(config)
    assert "-Dsonar.host.url=https://sonar.example.com" in args
    assert "-Dsonar.projectKey=my-proj" in args
    assert "-Dsonar.python.coverage.reportPaths=coverage.xml" in args
    assert all("secret" not in a for a in args)  # token never on argv


def test_skips_without_url():
    result = run_scanner(SonarConfig(host_url="", token="t"))
    assert not result.ok
    assert "host" in result.message.lower()


def test_skips_without_token():
    result = run_scanner(SonarConfig(host_url="https://s", token=""))
    assert not result.ok
    assert "token" in result.message.lower()


def test_token_passed_via_env():
    seen_env = {}

    def runner(argv, cwd, env):
        seen_env.update(env)
        return _completed(0)

    result = run_scanner(SonarConfig(host_url="https://s", token="abc"), base_env={}, runner=runner)
    assert result.ok
    assert seen_env["SONAR_TOKEN"] == "abc"


def test_missing_binary_is_non_blocking():
    def runner(argv, cwd, env):
        raise FileNotFoundError("sonar-scanner")

    result = run_scanner(SonarConfig(host_url="https://s", token="t"), base_env={}, runner=runner)
    assert not result.ok
    assert "not found" in result.message


def test_nonzero_exit_is_non_blocking():
    result = run_scanner(
        SonarConfig(host_url="https://s", token="t"),
        base_env={},
        runner=lambda *_: _completed(1, stderr="boom"),
    )
    assert not result.ok
    assert "boom" in result.message


# ── The guards that stop a run that cannot succeed ───────────────────────────────


class TestRequiredProperties:
    """Java maps a Sonar coverage property but cannot actually be scanned by the CLI.

    `sonar-scanner -Dsonar.sources=.` over a Java repo dies with "please provide
    compiled classes with sonar.java.binaries", and Sonar separately documents that the
    CLI scanner should not be used on Maven projects. Shipping the property without the
    guard means every Java consumer gets a failing scanner run and a green build.
    """

    def test_java_without_binaries_is_reported_as_missing(self):
        from brimyr.cli import _missing_sonar_props
        from brimyr.detect import ecosystem

        missing = _missing_sonar_props([ecosystem("java")], [])
        assert missing == {"Java / JVM": ("sonar.java.binaries",)}  # nosec B101

    def test_supplying_it_clears_the_guard(self):
        from brimyr.cli import _missing_sonar_props
        from brimyr.detect import ecosystem

        args = ["-Dsonar.java.binaries=target/classes"]
        assert _missing_sonar_props([ecosystem("java")], args) == {}  # nosec B101

    def test_ecosystems_without_requirements_are_never_flagged(self):
        from brimyr.cli import _missing_sonar_props
        from brimyr.detect import ecosystem

        pair = [ecosystem("python"), ecosystem("javascript")]
        assert _missing_sonar_props(pair, []) == {}  # nosec B101


class TestProjectKey:
    """No key means `sonar-scanner` aborts — most of why the leg never ran for anyone."""

    def test_defaults_from_the_repo_slug(self, monkeypatch):
        from brimyr.cli import _default_project_key

        monkeypatch.setenv("GITHUB_REPOSITORY", "MagmaMoose/brimyr")
        # Sonar keys may not contain '/', so the slug cannot be used as-is.
        assert _default_project_key() == "MagmaMoose_brimyr"  # nosec B101

    def test_empty_outside_actions(self, monkeypatch):
        from brimyr.cli import _default_project_key

        monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
        assert _default_project_key() == ""  # nosec B101


class TestDotnetStrategy:
    def test_dotnet_declares_the_wrapping_strategy_and_a_real_property(self):
        from brimyr.detect import SonarStrategy, ecosystem

        eco = ecosystem("dotnet")
        assert eco.sonar_strategy is SonarStrategy.DOTNET  # nosec B101
        # `reportsPaths` — plural, unlike the python/javascript properties. The singular
        # form is silently ignored by Sonar.
        assert eco.sonar_property == "sonar.cs.cobertura.reportsPaths"  # nosec B101
        assert eco.sonar_report_globs  # nosec B101 - declared at begin, so must be a glob
        assert "--no-incremental" in eco.sonar_build_command  # nosec B101

    def test_every_other_ecosystem_stays_a_post_step(self):
        from brimyr.detect import ECOSYSTEMS, SonarStrategy

        cli = [e.key for e in ECOSYSTEMS if e.sonar_strategy is SonarStrategy.CLI]
        assert set(cli) == {"python", "javascript", "java"}  # nosec B101


class TestEscapeHatchFeedsSonar:
    """`coverage_file` used to hand Sonar an empty mapping.

    A full analysis then ran and reported NO coverage at all, silently. Sonar shows the
    project at 0%, which reads as a real measurement rather than a missing one.
    """

    def _args(self, url="https://sonar.example"):
        import argparse

        return argparse.Namespace(sonar_url=url)

    def test_jacoco_and_lcov_resolve_from_the_format(self):
        from pathlib import Path

        from brimyr.cli import _sonar_paths_for_specs
        from brimyr.detect import CoverageFormat

        specs = [
            (Path("m/target/site/jacoco/jacoco.xml"), CoverageFormat.JACOCO),
            (Path("coverage/lcov.info"), CoverageFormat.LCOV),
        ]
        out = _sonar_paths_for_specs(specs, self._args())
        assert out["sonar.coverage.jacoco.xmlReportPaths"] == (  # nosec B101
            "m/target/site/jacoco/jacoco.xml",
        )
        assert out["sonar.javascript.lcov.reportPaths"] == ("coverage/lcov.info",)  # nosec B101

    def test_cobertura_is_not_guessed(self, capsys):
        """Python and .NET both emit Cobertura under different properties.

        Guessing would ship the coverage under a property Sonar ignores, which looks
        exactly like success. Warn and name the way out instead.
        """
        from pathlib import Path

        from brimyr.cli import _sonar_paths_for_specs
        from brimyr.detect import CoverageFormat

        out = _sonar_paths_for_specs(
            [(Path("coverage.xml"), CoverageFormat.COBERTURA)], self._args()
        )
        assert out == {}  # nosec B101
        assert "sonar_args" in capsys.readouterr().err  # nosec B101 - names the fix

    def test_nothing_happens_without_a_sonar_url(self):
        from pathlib import Path

        from brimyr.cli import _sonar_paths_for_specs
        from brimyr.detect import CoverageFormat

        specs = [(Path("a/jacoco.xml"), CoverageFormat.JACOCO)]
        assert _sonar_paths_for_specs(specs, self._args(url="")) == {}  # nosec B101
