"""Unit tests for the Go coverage-profile parser (brimyr.coverage.gocover)."""

from __future__ import annotations

import pytest

from brimyr.coverage.gocover import GoCoverError, block_lines, is_gocover, parse_gocover

# A real `go test -coverprofile` run over this source reported 66.7% of statements:
# the `if` condition and the final `return` ran, the early `return 0` did not.
SOURCE = """package a

// Add adds.
func Add(x, y int) int {
\t// a comment inside the body

\tif x > 100 {
\t\treturn 0
\t}
\treturn x + y
}

func Empty() {}
""".splitlines()

PROFILE = """mode: set
example.com/m/a/a.go:4.24,7.13 1 1
example.com/m/a/a.go:7.13,9.3 1 0
example.com/m/a/a.go:10.2,10.14 1 1
example.com/m/a/a.go:13.15,13.16 0 0
"""


def _source(lines):
    return lambda path: lines


def test_a_profile_is_recognised_by_its_mode_line():
    assert is_gocover("mode: set\nx.go:1.1,2.2 1 1\n")
    assert is_gocover("\n  mode: atomic\n")
    assert not is_gocover("TN:\nSF:a.ts\n")
    assert not is_gocover("mode: sideways\n")
    assert not is_gocover("")


def test_trimmed_blocks_reproduce_gos_own_statement_percentage():
    """The whole reason the source is read. Untrimmed, the `func` line, the comment,
    the blank line and both closing braces all count, and the file reads 71% where
    `go test -cover` itself printed 66.7%."""
    report = parse_gocover(PROFILE, source_lines=_source(SOURCE))
    f = report.get("example.com/m/a/a.go")
    assert f.covered == {7, 10}
    assert f.uncovered == {8}
    assert round(100 * len(f.covered) / len(f.executable), 1) == 66.7


def test_without_the_source_a_block_keeps_its_whole_span():
    # Over-counting braces is the lesser failure next to dropping the file.
    f = parse_gocover(PROFILE).get("example.com/m/a/a.go")
    assert f.covered == {4, 5, 6, 7, 10}
    assert f.uncovered == {8, 9}


def test_a_zero_statement_block_is_not_code():
    # `func Empty() {}` still gets a block, with numStmt 0.
    f = parse_gocover(PROFILE, source_lines=_source(SOURCE)).get("example.com/m/a/a.go")
    assert 13 not in f.executable


def test_the_import_path_is_resolved_by_the_injected_resolver():
    report = parse_gocover(
        PROFILE,
        resolve_path=lambda path: path.removeprefix("example.com/m/"),
        source_lines=lambda path: SOURCE if path == "a/a.go" else None,
    )
    assert report.get("a/a.go").covered == {7, 10}
    assert report.get("example.com/m/a/a.go") is None


def test_a_one_line_function_counts_its_line():
    lines = ['func (c C) Name() string { return c.name + "-rw" }']
    assert block_lines((1, 26), (1, 51), lines) == (1,)


def test_a_block_of_nothing_but_braces_has_no_lines():
    assert block_lines((1, 10), (2, 2), ["func f() {", "}"]) == ()


def test_a_trailing_comment_on_the_opening_line_is_not_the_first_statement():
    lines = ["func f() { // why", "\tx := 1", "}"]
    assert block_lines((1, 10), (2, 8), lines) == (2,)


def test_a_multi_line_statement_keeps_its_inner_closing_brace():
    # A composite literal's `}` is part of the statement; only the BLOCK's own edge
    # braces are trimmed.
    lines = ["func f() T {", "\tx := T{", "\t\tA: 1,", "\t}", "\treturn x", "}"]
    assert block_lines((1, 12), (5, 10), lines) == (2, 3, 4, 5)


def test_a_span_past_the_end_of_the_source_is_left_untrimmed():
    # The file on disk is not the file that was measured; trimming it would be guessing.
    assert block_lines((3, 1), (9, 2), ["a", "b"]) == tuple(range(3, 10))


def test_a_line_two_blocks_share_is_covered_if_either_ran():
    profile = "mode: set\nm/x.go:1.1,2.10 1 0\nm/x.go:2.10,3.2 1 1\n"
    f = parse_gocover(profile).get("m/x.go")
    assert 2 in f.covered


def test_count_mode_hits_and_repeated_blocks_merge_covered_wins():
    # `-coverpkg` and concatenated profiles repeat blocks with different counts.
    profile = "mode: count\nm/x.go:1.1,1.9 1 0\nmode: count\nm/x.go:1.1,1.9 1 7\n"
    assert parse_gocover(profile).get("m/x.go").covered == {1}


def test_a_windows_path_keeps_its_drive_colon():
    profile = "mode: set\nC:/src/m/x.go:1.1,1.9 1 1\n"
    assert parse_gocover(profile).get("C:/src/m/x.go").covered == {1}


def test_malformed_records_are_skipped_not_fatal():
    profile = "mode: set\ngarbage\nm/x.go:1.1,1.9 one 1\nm/x.go:2.1,2.9 1 1\n"
    assert parse_gocover(profile).get("m/x.go").covered == {2}


def test_text_that_is_not_a_profile_is_an_error_not_an_empty_report():
    with pytest.raises(GoCoverError):
        parse_gocover("<coverage/>")
