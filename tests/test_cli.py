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
    code = main(
        [
            "coverage",
            "--coverage-file",
            str(cov),
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--min-lines",
            "0",
        ]
    )
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
            # 2 changed lines is under the default sample-size floor; this test is about
            # the JSON shape and the fail verdict, not about the floor.
            "--min-lines",
            "0",
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
    code = main(
        [
            "coverage",
            "--coverage-file",
            str(cov),
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--min-lines",
            "0",
        ]
    )
    assert code == 1  # nosec B101


def test_jacoco_pass(java_repo, tmp_path):
    repo_dir, base = java_repo
    cov = tmp_path / "jacoco.xml"
    _jacoco(cov, {4: 1, 5: 1})
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 0  # nosec B101


def test_explicit_jacoco_format_suffix_wins(java_repo, tmp_path):
    """`path:jacoco` must work even when the file is named something unsniffable."""
    repo_dir, base = java_repo
    cov = tmp_path / "merged.report"
    _jacoco(cov, {4: 1, 5: 0})  # 50% < 80%
    code = main(
        [
            "coverage",
            "--coverage-file",
            f"{cov}:jacoco",
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--min-lines",
            "0",
        ]
    )
    assert code == 1  # nosec B101


def test_cobertura_xml_still_sniffs_as_cobertura(repo, tmp_path):
    """The sniff must not regress the format every existing user is on."""
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    code = main(["coverage", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)])
    assert code == 0  # nosec B101


# ── the quality gate: `brimyr lint`, and `brimyr ci --quality-*` ─────────────


def _quality_counts(path, net_new=1, total=3, levels=None, **extra):
    """Write a counts document shaped like `chargate filter-sarif --counts-json`."""
    import json

    payload = {
        "schema_version": 1,
        "net_new_count": net_new,
        "total_count": total,
        "pre_existing_count": total - net_new,
        "suppressed_count": 0,
        "per_level_total": {"warning": total},
        "per_level_net_new": {"warning": net_new} if levels is None else levels,
    }
    payload.update(extra)
    path.write_text(json.dumps(payload))
    return path


def _quality_sarif(path, count=1):
    """Write a net-new SARIF holding ``count`` results."""
    import json

    results = [
        {
            "ruleId": f"Q{i:03d}",
            "locations": [
                {
                    "physicalLocation": {
                        "artifactLocation": {"uri": "a.py"},
                        "region": {"startLine": 4 + i},
                    }
                }
            ],
        }
        for i in range(count)
    ]
    path.write_text(json.dumps({"version": "2.1.0", "runs": [{"results": results}]}))
    return path


def test_lint_is_report_only_by_default(tmp_path, capsys):
    counts = _quality_counts(tmp_path / "counts.json", net_new=4, levels={"error": 4})
    code = main(["lint", "--counts", str(counts)])
    assert code == 0  # nosec B101
    assert "report-only" in capsys.readouterr().err  # nosec B101


def test_lint_blocks_at_the_requested_level(tmp_path):
    counts = _quality_counts(tmp_path / "counts.json", net_new=2, levels={"error": 2})
    assert main(["lint", "--counts", str(counts), "--fail-on", "error", "--quiet"]) == 1  # nosec B101


def test_lint_does_not_block_below_the_requested_level(tmp_path):
    counts = _quality_counts(tmp_path / "counts.json", net_new=2, levels={"warning": 2})
    assert main(["lint", "--counts", str(counts), "--fail-on", "error", "--quiet"]) == 0  # nosec B101


def test_lint_no_gate_never_blocks(tmp_path):
    counts = _quality_counts(tmp_path / "counts.json", net_new=2, levels={"error": 2})
    args = ["lint", "--counts", str(counts), "--fail-on", "any", "--no-gate", "--quiet"]
    assert main(args) == 0  # nosec B101


def test_lint_hard_fails_on_an_unknown_schema_version(tmp_path, capsys):
    # Version skew across the process boundary. Refusing to gate is the point: a
    # document brimyr cannot read must not be reported as a clean one.
    counts = _quality_counts(tmp_path / "counts.json", schema_version=99)
    assert main(["lint", "--counts", str(counts), "--fail-on", "any"]) == 2  # nosec B101
    assert "schema_version" in capsys.readouterr().err  # nosec B101


def test_lint_hard_fails_on_a_missing_counts_file(tmp_path, capsys):
    assert main(["lint", "--counts", str(tmp_path / "nope.json"), "--quiet"]) == 2  # nosec B101
    assert "could not read" in capsys.readouterr().err  # nosec B101


def test_lint_hard_fails_when_the_sarif_contradicts_the_counts(tmp_path, capsys):
    # Chargate writes both files from one in-memory result, so a mismatch means one is
    # stale or truncated — and the one that reads as a pass is the one to distrust.
    counts = _quality_counts(tmp_path / "counts.json", net_new=3, levels={"warning": 3})
    sarif = _quality_sarif(tmp_path / "net-new.sarif", count=1)
    code = main(["lint", "--counts", str(counts), "--findings", str(sarif), "--fail-on", "any"])
    assert code == 2  # nosec B101
    assert "stale or truncated" in capsys.readouterr().err  # nosec B101


def test_lint_writes_a_json_summary(tmp_path):
    import json

    counts = _quality_counts(tmp_path / "counts.json", net_new=2, levels={"error": 2})
    out = tmp_path / "quality.json"
    main(["lint", "--counts", str(counts), "--fail-on", "error", "--json-out", str(out), "--quiet"])
    data = json.loads(out.read_text())
    assert data["net_new_count"] == 2  # nosec B101
    assert data["blocking_count"] == 2  # nosec B101
    assert data["gate_result"] == "fail"  # nosec B101
    assert data["counts_schema_version"] == 1  # nosec B101


def test_lint_lists_the_findings_it_read(tmp_path, capsys):
    counts = _quality_counts(tmp_path / "counts.json", net_new=2, levels={"error": 2})
    sarif = _quality_sarif(tmp_path / "net-new.sarif", count=2)
    main(["lint", "--counts", str(counts), "--findings", str(sarif), "--fail-on", "error"])
    err = capsys.readouterr().err
    assert "a.py:4 [Q000]" in err  # nosec B101
    assert "a.py:5 [Q001]" in err  # nosec B101


def test_ci_folds_the_quality_verdict_into_one_run(repo, tmp_path, monkeypatch):
    """A clean coverage gate does not launder a blocking quality finding."""
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})  # 100% patch coverage
    counts = _quality_counts(tmp_path / "counts.json", net_new=2, levels={"error": 2})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

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
            "--quality-counts",
            str(counts),
            "--quality-fail-on",
            "error",
            "--quiet",
        ]
    )
    assert code == 1  # nosec B101
    # One consolidated view: both verdicts in the same summary block.
    text = summary.read_text()
    assert "Brimyr: Quality Assurance" in text  # nosec B101
    assert "Brimyr: Net-new findings" in text  # nosec B101


def test_ci_quality_report_only_leaves_a_passing_run_passing(repo, tmp_path):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    counts = _quality_counts(tmp_path / "counts.json", net_new=9, levels={"error": 9})
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
            "--quality-counts",
            str(counts),
            "--quiet",
        ]
    )
    assert code == 0  # nosec B101


def test_ci_a_broken_test_run_still_outranks_a_quality_pass(repo, tmp_path):
    # 0 < 1 < 2: a tool error must not be downgraded to a plain gate failure, and a
    # clean quality half must not upgrade it to a pass.
    repo_dir, base = repo
    counts = _quality_counts(tmp_path / "counts.json", net_new=0, levels={})
    code = main(
        [
            "ci",
            "--mode",
            "pr",
            "--ecosystem",
            "python",
            "--test-command",
            "false",
            "--base",
            base,
            "--repo",
            str(repo_dir),
            "--quality-counts",
            str(counts),
            "--quiet",
        ]
    )
    assert code == 2  # nosec B101


def test_ci_hard_fails_when_the_quality_scan_left_no_counts_file(repo, tmp_path, capsys):
    """A quality scan that never ran must not read as a clean quality half."""
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
            "--quality-counts",
            str(tmp_path / "never-written.json"),
            "--quiet",
        ]
    )
    assert code == 2  # nosec B101
    assert "could not read" in capsys.readouterr().err  # nosec B101


def test_ci_without_quality_counts_is_unchanged(repo, tmp_path, monkeypatch):
    """The whole feature is additive: no flag, no quality block, no new outputs."""
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    summary = tmp_path / "summary.md"
    outputs = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))

    code = main(
        ["ci", "--mode", "pr", "--coverage-file", str(cov), "--base", base, "--repo", str(repo_dir)]
    )
    assert code == 0  # nosec B101
    assert "Brimyr: Net-new findings" not in summary.read_text()  # nosec B101
    assert "quality_" not in outputs.read_text()  # nosec B101


def test_ci_baseline_never_gates_on_quality_either(repo, tmp_path):
    repo_dir, _base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    counts = _quality_counts(tmp_path / "counts.json", net_new=5, levels={"error": 5})
    code = main(
        [
            "ci",
            "--mode",
            "baseline",
            "--coverage-file",
            str(cov),
            "--repo",
            str(repo_dir),
            "--quality-counts",
            str(counts),
            "--quality-fail-on",
            "any",
            "--quiet",
        ]
    )
    assert code == 0  # nosec B101


def test_ci_emits_the_quality_outputs(repo, tmp_path, monkeypatch):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    counts = _quality_counts(tmp_path / "counts.json", net_new=3, levels={"warning": 3})
    outputs = tmp_path / "outputs.txt"
    monkeypatch.setenv("GITHUB_OUTPUT", str(outputs))

    main(
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
            "--quality-counts",
            str(counts),
            "--quality-fail-on",
            "error",
            "--quiet",
        ]
    )
    text = outputs.read_text()
    assert "quality_net_new_count=3" in text  # nosec B101
    assert "quality_blocking_count=0" in text  # nosec B101
    assert "quality_gate_result=pass" in text  # nosec B101
    # A report-only pass and a genuinely clean one must be distinguishable downstream.
    assert "quality_fail_on=error" in text  # nosec B101


def test_lint_scan_broken_is_an_error_without_reading_anything(tmp_path, capsys):
    """A scan that never completed must not read as zero findings.

    `chargate ci` writes its counts JSON *before* it decides whether the scan produced
    any runs, so a failed scan leaves a well-formed row of zeros behind. Parsing it
    would report a clean quality half over a scan that never happened, so the flag
    skips the read entirely.
    """
    zeros = _quality_counts(tmp_path / "counts.json", net_new=0, total=0, levels={})
    assert main(["lint", "--counts", str(zeros), "--quiet"]) == 0  # nosec B101
    code = main(["lint", "--counts", str(zeros), "--scan-broken"])
    assert code == 2  # nosec B101
    assert "BROKEN scan" in capsys.readouterr().err  # nosec B101


def test_ci_quality_scan_broken_is_an_error_and_still_reports_coverage(repo, tmp_path, monkeypatch):
    repo_dir, base = repo
    cov = tmp_path / "coverage.xml"
    _cobertura(cov, {4: 1, 5: 1})
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))

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
            "--quality-scan-broken",
            "--quiet",
        ]
    )
    assert code == 2  # nosec B101
    text = summary.read_text()
    # The half that DID work is still reported — a broken quality scan must not erase it.
    assert "Brimyr: Quality Assurance" in text  # nosec B101
    assert "The quality scan did not complete" in text  # nosec B101


def test_lint_warns_when_the_scan_ran_fewer_linters_than_asked(tmp_path, capsys):
    counts = _quality_counts(tmp_path / "counts.json", net_new=0, total=0, levels={})
    code = main(
        [
            "lint",
            "--counts",
            str(counts),
            "--fail-on",
            "any",
            "--scan-note",
            "JAVA_PMD (no image known)",
        ]
    )
    assert code == 0  # nosec B101
    err = capsys.readouterr().err
    assert "was not complete" in err  # nosec B101
    assert "JAVA_PMD" in err  # nosec B101


def test_lint_scan_broken_does_not_need_a_counts_file_at_all(capsys):
    # A failed scan may never have written one; demanding the path would be a usage
    # error raised at the exact moment the tool is being told the scan failed.
    assert main(["lint", "--scan-broken", "--quiet"]) == 2  # nosec B101
    assert main(["lint", "--quiet"]) == 2  # nosec B101
    assert "--counts is required" in capsys.readouterr().err  # nosec B101
