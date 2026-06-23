"""Unit tests for the coverage model + merge (brimyr.coverage.model)."""

from __future__ import annotations

from brimyr.coverage.model import CoverageBuilder, merge_reports


def test_builder_covered_wins_over_uncovered():
    b = CoverageBuilder()
    b.record("a.py", 1, 0)
    b.record("a.py", 1, 5)  # later (or earlier) hit makes it covered
    report = b.build()
    assert report.get("a.py").covered == frozenset({1})
    assert report.get("a.py").uncovered == frozenset()


def test_builder_normalizes_paths():
    b = CoverageBuilder()
    b.record("./a/b.py", 1, 1)
    assert b.build().get("a/b.py") is not None


def test_builder_ignores_nonpositive_lines():
    b = CoverageBuilder()
    b.record("a.py", 0, 1)
    b.record("a.py", -3, 1)
    assert len(b.build()) == 0


def test_merge_reports_covered_wins_across_reports():
    r1 = _report({"a.py": {1: 0, 2: 1}})
    r2 = _report({"a.py": {1: 1, 3: 0}})  # line 1 covered here
    merged = merge_reports([r1, r2])
    file_cov = merged.get("a.py")
    assert file_cov.covered == frozenset({1, 2})
    assert file_cov.uncovered == frozenset({3})


def test_merge_reports_disjoint_files():
    merged = merge_reports([_report({"a.py": {1: 1}}), _report({"b.py": {2: 0}})])
    assert {f.path for f in merged.files} == {"a.py", "b.py"}


def _report(files):
    b = CoverageBuilder()
    for path, line_hits in files.items():
        for line, hits in line_hits.items():
            b.record(path, line, hits)
    return b.build()
