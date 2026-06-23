"""Integration tests for the CLI (brimyr.cli) over a real git repo."""

from __future__ import annotations

import subprocess

import pytest

from brimyr.cli import main


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": _path(),
        },
    )


def _path():
    import os

    return os.environ.get("PATH", "")


def _rev(repo):
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A git repo: base commit (a.py 3 lines) then a head commit adding lines 4-5."""
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("1\n2\n3\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _rev(tmp_path)
    (tmp_path / "a.py").write_text("1\n2\n3\n4\n5\n")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "head")
    return tmp_path, base


def _cobertura(path, lines):
    body = "".join(f'<line number="{n}" hits="{h}"/>' for n, h in lines.items())
    path.write_text(
        f"<coverage><packages><package><classes>"
        f'<class filename="a.py"><lines>{body}</lines></class>'
        f"</classes></package></packages></coverage>"
    )


def test_coverage_pass(repo, tmp_path):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})  # both changed lines covered -> 100%
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 0


def test_coverage_fail_below_threshold(repo, tmp_path):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 0})  # one of two covered -> 50% < 80%
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 1


def test_coverage_custom_threshold(repo, tmp_path):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 0})  # 50%
    code = main(
        [
            "coverage",
            "--coverage-file",
            str(cov),
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--threshold",
            "50",
        ]
    )
    assert code == 0


def test_coverage_no_gate_reports_only(repo, tmp_path):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 0, 5: 0})  # 0%
    code = main(
        [
            "coverage",
            "--coverage-file",
            str(cov),
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--no-gate",
        ]
    )
    assert code == 0


def test_coverage_missing_file_is_error(repo, tmp_path):
    repo_dir, base = repo
    code = main(
        [
            "coverage",
            "--coverage-file",
            str(tmp_path / "nope.xml"),
            "--base",
            base,
            "--repo",
            str(repo_dir),
        ]
    )
    assert code == 2


def test_ci_escape_hatch_pr_mode(repo, tmp_path):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    code = main(
        [
            "ci",
            "--mode",
            "pr",
            "--coverage-file",
            str(cov),
            "--base",
            base,
            "--repo",
            str(repo_dir),
        ]
    )
    assert code == 0


def test_ci_baseline_never_gates(repo, tmp_path):
    repo_dir, _base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 0, 5: 0})  # 0% but baseline does not gate
    code = main(["ci", "--mode", "baseline", "--coverage-file", str(cov), "--repo", str(repo_dir)])
    assert code == 0


def test_ci_broken_run_is_error(repo, tmp_path):
    """A broken run (tests fail / no coverage file) exits 2 and reports an error.

    Drives a broken run end-to-end through ``brimyr ci``: a test command that exits
    non-zero and emits no coverage file. The exit code, the step outputs, and the
    JSON artifact must all agree it is an *error* — never a misleading 0%/100% pass.
    """
    import json

    repo_dir, base = repo
    out = tmp_path / "out.json"
    code = main(
        [
            "ci",
            "--mode",
            "pr",
            "--ecosystem",
            "python",
            "--test-command",
            "false",  # exits non-zero, produces no coverage.xml -> broken run
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--json-out",
            str(out),
            "--quiet",
        ]
    )
    assert code == 2
    assert json.loads(out.read_text())["gate_result"] == "error"


def test_json_out_written(repo, tmp_path):
    import json

    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 0})
    out = tmp_path / "out.json"
    main(
        [
            "coverage",
            "--coverage-file",
            str(cov),
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--json-out",
            str(out),
        ]
    )
    data = json.loads(out.read_text())
    assert data["total_lines"] == 2
    assert data["covered_lines"] == 1
    assert data["gate_result"] == "fail"


def test_version(capsys):
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip()
