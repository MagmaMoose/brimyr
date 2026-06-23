"""Unit tests for the gate decision (brimyr.gate)."""

from __future__ import annotations

import pytest

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.patch import compute_patch_coverage
from brimyr.gate import EXIT_BLOCKED, EXIT_ERROR, EXIT_OK, decide_gate


def _patch(make_report, covered, total):
    """Build a PatchCoverage with `covered`/`total` changed executable lines."""
    lines = {i: (1 if i <= covered else 0) for i in range(1, total + 1)}
    diff = DiffIndex((FileDiff("a.py", "added", ((1, total),)),)) if total else DiffIndex(())
    report = make_report({"a.py": lines}) if total else make_report({})
    return compute_patch_coverage(diff, report)


def test_pass_at_threshold(make_report):
    patch = _patch(make_report, covered=8, total=10)  # 80%
    decision = decide_gate(patch, 80.0)
    assert not decision.failed
    assert decision.exit_code == EXIT_OK


def test_fail_below_threshold(make_report):
    patch = _patch(make_report, covered=7, total=10)  # 70%
    decision = decide_gate(patch, 80.0)
    assert decision.failed
    assert decision.exit_code == EXIT_BLOCKED


def test_broken_run_is_error_not_zero_percent(make_report):
    patch = _patch(make_report, covered=0, total=0)
    decision = decide_gate(patch, 80.0, broken=True)
    assert not decision.failed  # not a gate fail
    assert decision.broken
    assert decision.exit_code == EXIT_ERROR


def test_vacuous_pass_when_nothing_changed(make_report):
    patch = _patch(make_report, covered=0, total=0)
    decision = decide_gate(patch, 80.0)
    assert not decision.failed
    assert decision.percent == 100.0
    assert decision.exit_code == EXIT_OK


def test_baseline_never_gates(make_report):
    patch = _patch(make_report, covered=1, total=10)  # 10%, would fail in PR mode
    decision = decide_gate(patch, 80.0, gate=False)
    assert not decision.failed
    assert decision.exit_code == EXIT_OK


def test_invalid_threshold_raises(make_report):
    patch = _patch(make_report, covered=1, total=1)
    with pytest.raises(ValueError, match="threshold"):
        decide_gate(patch, 150.0)
