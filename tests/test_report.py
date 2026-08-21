"""Unit tests for the report renderer (brimyr.report)."""

from __future__ import annotations

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.patch import compute_patch_coverage
from brimyr.detect import ecosystem
from brimyr.gate import decide_gate
from brimyr.modes import Mode
from brimyr.quality import decide_quality_gate, parse_counts
from brimyr.report import render_quality_summary, render_summary


def _decision(make_report, covered, total, threshold=80.0, **kw):
    lines = {i: (1 if i <= covered else 0) for i in range(1, total + 1)}
    diff = DiffIndex((FileDiff("a.py", "added", ((1, total),)),)) if total else DiffIndex(())
    patch = compute_patch_coverage(diff, make_report({"a.py": lines} if total else {}))
    # These fixtures are deliberately small (10 lines) to keep them readable, which puts
    # them under the default sample-size floor. Default to 0 here so each test exercises
    # the rendering it was written for; the floor has its own tests.
    kw.setdefault("min_lines", 0)
    return decide_gate(patch, threshold, **kw)


def test_pass_summary(make_report):
    decision = _decision(make_report, 9, 10)
    out = render_summary(decision, Mode.PR, ecosystems=[ecosystem("python")])
    assert "**Gate:** `pass`" in out
    assert "90.0%" in out
    assert "Python" in out


def test_fail_summary_lists_missing(make_report):
    decision = _decision(make_report, 5, 10)
    out = render_summary(decision, Mode.PR)
    assert "**Gate:** `fail`" in out
    assert "below" in out.lower()
    assert "`a.py`" in out  # missing lines listed for the file


def test_broken_summary(make_report):
    decision = _decision(make_report, 0, 0, broken=True)
    out = render_summary(decision, Mode.PR, broken=True)
    assert "**Gate:** `error`" in out
    assert "Broken test run" in out


def test_baseline_summary(make_report):
    decision = _decision(make_report, 5, 10, gate=False)
    out = render_summary(decision, Mode.BASELINE)
    assert "Baseline run" in out


def test_sonar_message_shown(make_report):
    decision = _decision(make_report, 9, 10)
    out = render_summary(decision, Mode.PR, sonar_message="analysis uploaded")
    assert "SonarQube" in out
    assert "analysis uploaded" in out


def test_below_min_lines_render(make_report):
    decision = _decision(make_report, covered=1, total=3, min_lines=20)
    out = render_summary(decision, Mode.PR)
    assert "⚪" in out  # nosec B101
    assert "3 changed executable line(s)" in out  # nosec B101
    assert "threshold was **not applied**" in out  # nosec B101


def test_summary_heading(make_report):
    decision = _decision(make_report, 9, 10)
    out = render_summary(decision, Mode.PR)
    first_line = out.split("\n")[0]
    assert first_line == "## Brimyr: Quality Assurance"  # nosec B101


# ── the quality block ────────────────────────────────────────────────────────


def _quality(net_new=2, total=5, levels=None, fail_on="none", **kw):
    counts = parse_counts(
        {
            "schema_version": 1,
            "net_new_count": net_new,
            "total_count": total,
            "pre_existing_count": total - net_new,
            "suppressed_count": kw.pop("suppressed", 0),
            "per_level_total": {"warning": total},
            "per_level_net_new": {"warning": net_new} if levels is None else levels,
        }
    )
    return decide_quality_gate(counts, fail_on, **kw)


def test_quality_report_only_says_so_rather_than_reading_as_a_pass():
    # A report-only gate that renders like a passing gate is how, six weeks later,
    # nobody can tell you whether quality is actually enforced.
    out = render_quality_summary(_quality(net_new=4, levels={"error": 4}))
    assert "**Gate:** `report-only`" in out
    assert "nothing blocks" in out
    assert "Net-new findings | **4**" in out


def test_quality_fail_summary_states_the_threshold_and_the_count():
    out = render_quality_summary(_quality(net_new=3, levels={"error": 3}, fail_on="error"))
    assert "**Gate:** `fail`" in out
    assert "3 net-new finding(s) at or above `error`" in out


def test_quality_pass_with_findings_below_the_threshold_still_reports_them():
    out = render_quality_summary(_quality(net_new=3, levels={"warning": 3}, fail_on="error"))
    assert "**Gate:** `pass`" in out
    assert "none at or above `error`" in out
    assert "Net-new findings | **3**" in out


def test_quality_clean_run():
    out = render_quality_summary(_quality(net_new=0, total=4, levels={}, fail_on="error"))
    assert "No net-new quality findings" in out


def test_quality_listing_is_collapsed_and_truncation_is_stated():
    # Summary only, never inline: chargate's per-finding review comments work because
    # security findings are sparse, and quality findings are not.
    listing = tuple(f"src/f{i}.py:{i} [Q{i}]" for i in range(25))
    decision = _quality(net_new=25, total=25, levels={"warning": 25}, fail_on="any")
    decision = decide_quality_gate(decision.counts, "any", listing=listing)
    out = render_quality_summary(decision)
    assert "<details><summary>Net-new findings</summary>" in out
    assert "`src/f0.py:0 [Q0]`" in out
    assert "and 5 more" in out


def test_quality_suppressed_row_only_appears_when_there_are_any():
    assert "Suppressed in source" not in render_quality_summary(_quality())
    assert "Suppressed in source" in render_quality_summary(_quality(suppressed=2))


def test_report_only_names_the_threshold_when_that_is_the_reason():
    out = render_quality_summary(_quality(net_new=2))
    assert "`quality_fail_on` is `none`" in out


def test_report_only_names_baseline_mode_when_that_is_the_reason():
    # Naming the threshold here would be a wrong answer: the threshold is `any` and the
    # reason nothing blocks is that a baseline run has no diff to gate.
    out = render_quality_summary(_quality(net_new=2, fail_on="any", gate=False))
    assert "no diff to gate (baseline mode)" in out
    assert "`quality_fail_on` is" not in out


def test_a_degraded_scan_says_so_next_to_the_count():
    counts = parse_counts(
        {
            "schema_version": 1,
            "net_new_count": 0,
            "total_count": 0,
            "pre_existing_count": 0,
            "per_level_net_new": {},
        }
    )
    decision = decide_quality_gate(counts, "any", scan_note="JAVA_PMD (no image known)")
    out = render_quality_summary(decision)
    assert "**Gate:** `pass`" in out
    # ...but the pass is qualified, because a smaller scan reporting nothing and a clean
    # repo produce the same zero.
    assert "The scan was not complete" in out
    assert "JAVA_PMD (no image known)" in out
