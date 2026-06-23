"""Unit tests for the LCOV parser (brimyr.coverage.lcov)."""

from __future__ import annotations

from brimyr.coverage.lcov import parse_lcov

LCOV = """\
TN:
SF:src/app.js
FN:1,foo
FNDA:3,foo
DA:1,3
DA:2,0
DA:3,1
BRDA:2,0,0,0
LF:3
LH:2
end_of_record
SF:src/util.ts
DA:10,5
DA:11,0
end_of_record
"""


def test_parses_covered_and_uncovered():
    report = parse_lcov(LCOV)
    app = report.get("src/app.js")
    assert app is not None
    assert app.covered == frozenset({1, 3})
    assert app.uncovered == frozenset({2})
    assert app.executable == frozenset({1, 2, 3})


def test_second_file_parsed():
    report = parse_lcov(LCOV)
    util = report.get("src/util.ts")
    assert util is not None
    assert util.covered == frozenset({10})
    assert util.uncovered == frozenset({11})


def test_da_outside_record_is_ignored():
    # A DA line before any SF: has no file to attach to.
    report = parse_lcov("DA:1,1\nSF:x.js\nDA:2,1\nend_of_record\n")
    assert report.get("x.js").covered == frozenset({2})
    assert len(report) == 1


def test_malformed_da_skipped():
    report = parse_lcov("SF:x.js\nDA:notanumber\nDA:5,1\nend_of_record\n")
    assert report.get("x.js").covered == frozenset({5})


def test_empty_input():
    assert len(parse_lcov("")) == 0
