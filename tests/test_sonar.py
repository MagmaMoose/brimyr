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
