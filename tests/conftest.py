"""Shared factories for the test suite: diffs and coverage reports."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.model import CoverageBuilder, CoverageReport


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
