"""Shared factories for the test suite: diffs and coverage reports."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.model import CoverageBuilder, CoverageReport


@pytest.fixture(autouse=True)
def _no_actions_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite from writing into a real GitHub Actions job summary.

    ``report.append_step_summary`` / ``report.write_outputs`` are no-ops unless
    ``GITHUB_STEP_SUMMARY`` / ``GITHUB_OUTPUT`` are set — but under Actions they
    always are, and the end-to-end CLI tests drive ``main()``, which writes a
    real summary block and a real set of ``gate_result``/``gate_failed`` outputs
    per test. That matters the moment brimyr gates brimyr: its own suite would
    inject bogus verdicts into the very job summary and outputs the gate step is
    about to publish. Tests that exercise the writers set these deliberately.
    """
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)


@pytest.fixture
def make_diff() -> Callable[..., DiffIndex]:
    def _make(*files: FileDiff) -> DiffIndex:
        return DiffIndex(tuple(files))

    return _make


@pytest.fixture
def added_file() -> Callable[..., FileDiff]:
    def _make(path: str, *ranges: tuple[int, int], status: str = "added") -> FileDiff:
        return FileDiff(path=path, status=status, added_ranges=tuple(ranges))

    return _make


@pytest.fixture
def make_report() -> Callable[..., CoverageReport]:
    def _make(files: dict[str, dict[int, int]]) -> CoverageReport:
        """files: {path: {line: hits}} -> CoverageReport."""
        builder = CoverageBuilder()
        for path, line_hits in files.items():
            for line, hits in line_hits.items():
                builder.record(path, line, hits)
        return builder.build()

    return _make
