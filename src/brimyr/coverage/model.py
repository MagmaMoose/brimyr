"""The coverage data model shared by every parser.

A :class:`CoverageReport` records, per file, which *executable* lines the test run
covered and which it missed. Parsers (lcov, Cobertura) build one through
:class:`CoverageBuilder`, which folds repeated line records with **covered-wins**
semantics — a line hit by any test in any report counts as covered. That makes
merging several reports (polyglot repos emit one per language; a suite may emit
unit + integration files) associative and order-independent.

Non-executable lines (blank lines, comments, braces) never appear here: coverage
tools only report executable lines, and patch coverage's denominator is exactly
those. This module is **pure** — no file or path I/O beyond string normalization.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from brimyr.coverage.diff import normalize_path


@dataclass(frozen=True)
class FileCoverage:
    """One file's executable-line coverage.

    ``covered`` and ``uncovered`` are disjoint sets of 1-based line numbers; their
    union is the file's executable lines as the coverage tool saw them.
    """

    path: str
    covered: frozenset[int]
    uncovered: frozenset[int]

    @property
    def executable(self) -> frozenset[int]:
        return self.covered | self.uncovered

    def is_covered(self, line: int) -> bool:
        return line in self.covered


@dataclass(frozen=True)
class CoverageReport:
    """Executable-line coverage for a set of files, keyed by normalized path."""

    files: tuple[FileCoverage, ...]

    def as_dict(self) -> dict[str, FileCoverage]:
        return {f.path: f for f in self.files}

    def get(self, path: str) -> FileCoverage | None:
        norm = normalize_path(path)
        for f in self.files:
            if f.path == norm:
                return f
        return None

    def __bool__(self) -> bool:
        return bool(self.files)

    def __len__(self) -> int:
        return len(self.files)


class CoverageBuilder:
    """Accumulate per-line hit counts, then :meth:`build` a report.

    ``record(path, line, hits)`` may be called repeatedly for the same line; the
    maximum hit count wins, so a line covered by *any* test ends up covered.
    """

    def __init__(self) -> None:
        self._hits: dict[str, dict[int, int]] = {}

    def record(self, path: str, line: int, hits: int) -> None:
        if line < 1:
            return
        norm = normalize_path(path)
        file_hits = self._hits.setdefault(norm, {})
        prev = file_hits.get(line)
        file_hits[line] = hits if prev is None else max(prev, hits)

    def build(self) -> CoverageReport:
        files: list[FileCoverage] = []
        for path, line_hits in self._hits.items():
            covered = frozenset(line for line, hits in line_hits.items() if hits > 0)
            uncovered = frozenset(line for line, hits in line_hits.items() if hits <= 0)
            files.append(FileCoverage(path=path, covered=covered, uncovered=uncovered))
        return CoverageReport(tuple(files))


def merge_reports(reports: Iterable[CoverageReport]) -> CoverageReport:
    """Combine several reports into one, covered-wins, per file path."""
    builder = CoverageBuilder()
    for report in reports:
        for file_cov in report.files:
            for line in file_cov.covered:
                builder.record(file_cov.path, line, 1)
            for line in file_cov.uncovered:
                builder.record(file_cov.path, line, 0)
    return builder.build()
