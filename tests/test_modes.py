"""Unit tests for run-mode resolution (brimyr.modes)."""

from __future__ import annotations

from brimyr.modes import Mode, resolve_mode


def test_explicit_mode_wins():
    assert resolve_mode("baseline", "pull_request") is Mode.BASELINE
    assert resolve_mode("pr", "push") is Mode.PR


def test_auto_uses_event_name():
    assert resolve_mode("auto", "pull_request") is Mode.PR
    assert resolve_mode("auto", "pull_request_target") is Mode.PR
    assert resolve_mode("auto", "push") is Mode.BASELINE
    assert resolve_mode("auto", "schedule") is Mode.BASELINE
    assert resolve_mode(None, None) is Mode.BASELINE


def test_only_pr_mode_gates():
    assert Mode.PR.gates is True
    assert Mode.BASELINE.gates is False
