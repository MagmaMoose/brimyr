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


@pytest.fixture
def java_repo(tmp_path):
    """A git repo whose head commit adds two lines to a Java source file.

    The path carries the Maven source-root prefix (`backend/src/main/java/...`) that a
    JaCoCo report does NOT — JaCoCo names files as `<package>/<sourcefile>`. Suffix
    matching in `coverage.patch` is what reconciles the two, so the fixture keeps the
    prefix rather than flattening it into something artificially easy to match.
    """
    src = tmp_path / "backend" / "src" / "main" / "java" / "nl" / "example"
    src.mkdir(parents=True)
    target = src / "Service.java"
    _git(tmp_path, "init", "-q")
    target.write_text("1\n2\n3\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "base")
    base = _rev(tmp_path)
    target.write_text("1\n2\n3\n4\n5\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "head")
    return tmp_path, base


def _jacoco(path, lines):
    body = "".join(f'<line nr="{n}" mi="{1 - c}" ci="{c}"/>' for n, c in lines.items())
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
        '<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">\n'
        '<report name="m"><package name="nl/example">'
        f'<sourcefile name="Service.java">{body}</sourcefile>'
        "</package></report>"
    )


def test_jacoco_xml_is_not_read_as_cobertura(java_repo, tmp_path):
    """The load-bearing test for Java support.

    `.xml` is the extension of BOTH formats. If a JaCoCo report is parsed as Cobertura
    it yields zero files, `Service.java` is then a file the report never mentions, its
    changed lines leave the denominator, and the gate returns a vacuous 100% PASS over
    code with no coverage at all. Exit 1 here is the whole point: the failure must be
    loud.
    """
    repo_dir, base = java_repo
    cov = tmp_path / "jacoco.xml"
    _jacoco(cov, {4: 0, 5: 0})  # neither changed line covered -> 0%
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 1


def test_jacoco_pass(java_repo, tmp_path):
    repo_dir, base = java_repo
    cov = tmp_path / "jacoco.xml"
    _jacoco(cov, {4: 1, 5: 1})
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 0


def test_explicit_jacoco_format_suffix_wins(java_repo, tmp_path):
    """`path:jacoco` must work even when the file is named something unsniffable."""
    repo_dir, base = java_repo
    cov = tmp_path / "merged.report"
    _jacoco(cov, {4: 1, 5: 0})  # 50% < 80%
    code = main(
        ["coverage", "--coverage-file", f"{cov}:jacoco", "--base", base, "--repo", str(repo_dir)]
    )
    assert code == 1


def test_cobertura_xml_still_sniffs_as_cobertura(repo, tmp_path):
    """The sniff must not regress the format every existing user is on."""
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 0
