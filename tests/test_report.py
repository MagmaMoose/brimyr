"""Unit tests for the report renderer (brimyr.report)."""

from __future__ import annotations

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.patch import compute_patch_coverage
from brimyr.detect import ecosystem
from brimyr.gate import decide_gate
from brimyr.modes import Mode
from brimyr.report import render_summary


def _decision(make_report, covered, total, threshold=80.0, **kw):
    lines = {i: (1 if i <= covered else 0) for i in range(1, total + 1)}
    diff = DiffIndex((FileDiff("a.py", "added", ((1, total),)),)) if total else DiffIndex(())
    patch = compute_patch_coverage(diff, make_report({"a.py": lines} if total else {}))
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
