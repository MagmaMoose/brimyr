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
    # min_lines=0 so this stays a test of the THRESHOLD. At the default floor of 20 a
    # 10-line diff is exempt, which would make this pass for an unrelated reason.
    decision = decide_gate(patch, 80.0, min_lines=0)
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


# ── the sample-size floor ────────────────────────────────────────────────────


def test_small_diff_is_not_gated(make_report):
    """Three changed lines, one uncovered, is 67% — a number that means nothing.

    SonarQube applies exactly this rule at exactly this number ("the conditions on
    coverage are ignored until the number of new lines to cover is at least 20"), so
    matching it keeps a Brimyr verdict and a Sonar verdict from disagreeing on small PRs.
    """
    patch = _patch(make_report, covered=2, total=3)  # 66.7%
    decision = decide_gate(patch, 80.0)
    assert decision.failed is False  # nosec B101
    assert decision.below_min_lines is True  # nosec B101
    assert decision.exit_code == EXIT_OK  # nosec B101


def test_the_floor_is_a_hole_and_is_reported_as_one(make_report):
    """A 19-line untested change passes. That is the deliberate cost of the rule.

    It is the same SHAPE of hole that makes Sonar's project-level gate unusable, just
    much smaller — which is why the decision carries `below_min_lines` and the summary
    says so out loud instead of printing a quiet pass.
    """
    patch = _patch(make_report, covered=0, total=19)  # 0%, and it passes
    decision = decide_gate(patch, 80.0)
    assert decision.failed is False  # nosec B101
    assert decision.below_min_lines is True  # nosec B101


def test_at_the_floor_the_gate_applies(make_report):
    patch = _patch(make_report, covered=0, total=20)
    decision = decide_gate(patch, 80.0)
    assert decision.failed is True  # nosec B101
    assert decision.below_min_lines is False  # nosec B101


def test_min_lines_zero_gates_everything(make_report):
    patch = _patch(make_report, covered=0, total=1)
    assert decide_gate(patch, 80.0, min_lines=0).failed is True  # nosec B101


def test_nothing_coverable_is_still_a_vacuous_pass_not_the_floor(make_report):
    """An empty diff and a small diff pass for DIFFERENT reasons; keep them distinct."""
    patch = _patch(make_report, covered=0, total=0)
    decision = decide_gate(patch, 80.0)
    assert decision.failed is False  # nosec B101
    assert decision.below_min_lines is False  # nosec B101 - vacuous, not "too small"


def test_a_broken_run_still_errors_under_the_floor(make_report):
    """The floor must never soften the broken-run rule into a pass."""
    patch = _patch(make_report, covered=0, total=3)
    decision = decide_gate(patch, 80.0, broken=True)
    assert decision.exit_code == EXIT_ERROR  # nosec B101


def test_negative_min_lines_rejected(make_report):
    patch = _patch(make_report, covered=1, total=2)
    with pytest.raises(ValueError, match="min_lines"):
        decide_gate(patch, 80.0, min_lines=-1)
