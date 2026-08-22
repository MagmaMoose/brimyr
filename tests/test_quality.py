"""Unit tests for the net-new quality gate (brimyr.quality).

Pure: every case here is a dict in, a verdict out. The one thing worth stating up
front is what most of these tests are *for* — every failure mode of a gate like this
is a silent pass, so the assertions that matter most are the ones proving brimyr
raises instead of returning a comfortable zero.
"""

from __future__ import annotations

import pytest

from brimyr.quality import (
    FAIL_ON_CHOICES,
    SUPPORTED_COUNTS_SCHEMA_VERSIONS,
    QualityInputError,
    broken_decision,
    check_findings_consistent,
    count_sarif_results,
    decide_quality_gate,
    parse_counts,
    read_finding_lines,
)


def _counts(net_new=2, total=5, levels=None, **extra):
    """A counts document shaped like the one `chargate filter-sarif` writes."""
    levels = {"warning": net_new} if levels is None else levels
    payload = {
        "schema_version": 1,
        "net_new_count": net_new,
        "total_count": total,
        "pre_existing_count": total - net_new,
        "suppressed_count": 0,
        "per_level_total": {"warning": total},
        "per_level_net_new": levels,
    }
    payload.update(extra)
    return payload


def _result(uri="a.py", line=7, rule="Q001"):
    return {
        "ruleId": rule,
        "locations": [
            {
                "physicalLocation": {
                    "artifactLocation": {"uri": uri},
                    "region": {"startLine": line},
                }
            }
        ],
    }


def _sarif(*results):
    return {"version": "2.1.0", "runs": [{"results": list(results)}]}


# ── parse_counts: the version boundary ───────────────────────────────────────


def test_parses_a_well_formed_counts_document():
    counts = parse_counts(_counts(net_new=2, total=5))
    assert counts.net_new == 2  # nosec B101
    assert counts.total == 5  # nosec B101
    assert counts.pre_existing == 3  # nosec B101
    assert counts.schema_version == 1  # nosec B101


def test_an_unknown_schema_version_is_an_error_not_a_pass():
    # The two sides of the process boundary release independently. A document from a
    # future chargate might mean something different by every key in it; guessing is
    # how a gate reports `pass` over findings it could not read.
    unknown = max(SUPPORTED_COUNTS_SCHEMA_VERSIONS) + 1
    with pytest.raises(QualityInputError, match="schema_version"):
        parse_counts(_counts() | {"schema_version": unknown})


def test_a_missing_schema_version_is_an_error_too():
    # An old chargate, which is version skew in exactly the same way — the file
    # predates the contract that makes reading it safe.
    payload = _counts()
    del payload["schema_version"]
    with pytest.raises(QualityInputError, match="schema_version"):
        parse_counts(payload)


def test_a_boolean_schema_version_is_not_an_integer_one():
    # `True` is an int subclass and would otherwise read as version 1.
    with pytest.raises(QualityInputError, match="schema_version"):
        parse_counts(_counts() | {"schema_version": True})


@pytest.mark.parametrize("payload", ["not a document", ["a", "list"], 7, None])
def test_a_non_object_counts_document_is_an_error(payload):
    with pytest.raises(QualityInputError):
        parse_counts(payload)


def test_a_missing_net_new_count_is_an_error():
    payload = _counts()
    del payload["net_new_count"]
    with pytest.raises(QualityInputError, match="net_new_count"):
        parse_counts(payload)


def test_counts_that_disagree_with_themselves_are_an_error():
    # Every net-new result contributes exactly one level, so these are the same number
    # counted twice. When they differ one of them is wrong, the gate cannot tell which,
    # and the wrong one is always the one that makes the PR pass.
    with pytest.raises(QualityInputError, match="disagrees with itself"):
        parse_counts(_counts(net_new=3, levels={"warning": 1}))


def test_a_non_integer_level_count_is_an_error():
    with pytest.raises(QualityInputError, match="per_level_net_new"):
        parse_counts(_counts(net_new=1, levels={"warning": "one"}))


def test_zero_net_new_with_an_empty_level_map_is_consistent():
    counts = parse_counts(_counts(net_new=0, total=4, levels={}))
    assert counts.net_new == 0  # nosec B101
    assert counts.pre_existing == 4  # nosec B101


# ── the threshold, in SARIF levels ───────────────────────────────────────────


def test_report_only_is_the_default_and_blocks_nothing():
    decision = decide_quality_gate(parse_counts(_counts(net_new=9, levels={"error": 9})))
    assert decision.fail_on == "none"  # nosec B101
    assert decision.failed is False  # nosec B101
    assert decision.gated is False  # nosec B101
    assert decision.exit_code == 0  # nosec B101


def test_fail_on_any_blocks_on_a_single_net_new_finding():
    decision = decide_quality_gate(parse_counts(_counts(net_new=1, levels={"note": 1})), "any")
    assert decision.failed is True  # nosec B101
    assert decision.blocking == 1  # nosec B101
    assert decision.exit_code == 1  # nosec B101


def test_fail_on_error_ignores_warnings_and_notes():
    counts = parse_counts(_counts(net_new=5, total=5, levels={"note": 2, "warning": 3}))
    decision = decide_quality_gate(counts, "error")
    assert decision.blocking == 0  # nosec B101
    assert decision.failed is False  # nosec B101
    # Still counted and still shown — reported, just not blocking.
    assert decision.counts.net_new == 5  # nosec B101


def test_fail_on_warning_blocks_on_warnings_and_errors_but_not_notes():
    counts = parse_counts(_counts(net_new=6, total=6, levels={"note": 3, "warning": 2, "error": 1}))
    assert decide_quality_gate(counts, "warning").blocking == 3  # nosec B101
    assert decide_quality_gate(counts, "error").blocking == 1  # nosec B101
    assert decide_quality_gate(counts, "note").blocking == 6  # nosec B101


def test_unlevelled_findings_only_block_at_any():
    # SARIF `level: none`. `note` is the lowest *level* threshold, so it deliberately
    # does not catch these; `any` is the setting that means literally any.
    counts = parse_counts(_counts(net_new=2, total=2, levels={"none": 2}))
    assert decide_quality_gate(counts, "note").blocking == 0  # nosec B101
    assert decide_quality_gate(counts, "any").blocking == 2  # nosec B101


def test_an_unrecognised_level_never_silently_ranks_high():
    # A level chargate did not produce (a future SARIF revision, a broken producer)
    # ranks at the floor, so it cannot spuriously trip an `error` threshold. It still
    # blocks at `any`, which is the setting that promises to.
    counts = parse_counts(_counts(net_new=1, total=1, levels={"catastrophe": 1}))
    assert decide_quality_gate(counts, "error").blocking == 0  # nosec B101
    assert decide_quality_gate(counts, "any").blocking == 1  # nosec B101


def test_gate_false_is_report_only_whatever_the_threshold():
    counts = parse_counts(_counts(net_new=4, levels={"error": 4}))
    decision = decide_quality_gate(counts, "any", gate=False)
    assert decision.failed is False  # nosec B101
    assert decision.gated is False  # nosec B101


def test_an_invalid_fail_on_is_an_error():
    with pytest.raises(QualityInputError, match="invalid fail_on"):
        decide_quality_gate(parse_counts(_counts()), "critical")


@pytest.mark.parametrize("choice", FAIL_ON_CHOICES)
def test_every_documented_fail_on_choice_is_accepted(choice: str):
    # And every one is reachable: a threshold nothing can ever satisfy is a gate that
    # silently never blocks, which is the failure mode this module exists to avoid.
    decision = decide_quality_gate(parse_counts(_counts(net_new=1, levels={"error": 1})), choice)
    assert decision.fail_on == choice  # nosec B101
    assert decision.failed is (choice != "none")  # nosec B101


def test_fail_on_is_case_insensitive_and_trimmed():
    decision = decide_quality_gate(parse_counts(_counts(net_new=1, levels={"error": 1})), " ERROR ")
    assert decision.fail_on == "error"  # nosec B101
    assert decision.failed is True  # nosec B101


# ── the filtered SARIF: display only, but its count must agree ───────────────


def test_counts_sarif_results_across_runs():
    sarif = {"runs": [{"results": [_result(), _result()]}, {"results": [_result()]}]}
    assert count_sarif_results(sarif) == 3  # nosec B101


def test_a_run_with_no_results_key_contributes_nothing():
    assert count_sarif_results({"runs": [{"tool": {}}]}) == 0  # nosec B101


def test_a_sarif_without_runs_is_an_error():
    # Not "zero findings". A report that lost its runs array is a broken report, and a
    # broken report that reads as zero is the whole silent-pass family in one line.
    with pytest.raises(QualityInputError, match="runs"):
        count_sarif_results({"version": "2.1.0"})


def test_a_sarif_holding_a_different_count_than_the_counts_is_an_error():
    counts = parse_counts(_counts(net_new=2, levels={"warning": 2}))
    with pytest.raises(QualityInputError, match="stale or truncated"):
        check_findings_consistent(counts, count_sarif_results(_sarif(_result())))


def test_a_sarif_agreeing_with_the_counts_passes_the_check():
    counts = parse_counts(_counts(net_new=2, levels={"warning": 2}))
    check_findings_consistent(counts, count_sarif_results(_sarif(_result(), _result())))


def test_finding_lines_render_path_line_and_rule():
    lines = read_finding_lines(_sarif(_result("src/a.py", 12, "E501")))
    assert lines == ("src/a.py:12 [E501]",)  # nosec B101


def test_finding_lines_degrade_rather_than_raise_on_odd_shapes():
    # Decoration on a verdict already decided by the counts. A finding with no location
    # should cost a vaguer line, never an exception.
    sarif = {"runs": [{"results": [{"ruleId": "X"}, {"locations": []}, "junk"]}]}
    assert read_finding_lines(sarif) == ("(no location) [X]", "(no location)")  # nosec B101


def test_the_listing_is_capped_and_says_how_many_it_dropped():
    many = _sarif(*[_result(f"f{i}.py", i) for i in range(30)])
    counts = parse_counts(_counts(net_new=30, total=30, levels={"warning": 30}))
    decision = decide_quality_gate(counts, "any", listing=read_finding_lines(many))
    assert len(decision.listing) == 20  # nosec B101
    assert decision.listing_truncated == 10  # nosec B101
    # The COUNT is never truncated — only the listing is.
    assert decision.blocking == 30  # nosec B101


# ── malformed inputs, exhaustively: every one of these must raise, not return 0 ──


def test_an_absent_level_map_reads_as_empty():
    payload = _counts(net_new=0, total=3)
    del payload["per_level_net_new"]
    del payload["per_level_total"]
    counts = parse_counts(payload)
    assert counts.per_level_net_new == {}  # nosec B101
    assert counts.per_level_total == {}  # nosec B101


def test_a_non_object_level_map_is_an_error():
    with pytest.raises(QualityInputError, match="non-object"):
        parse_counts(_counts() | {"per_level_net_new": ["warning", 2]})


@pytest.mark.parametrize(
    ("payload", "match"),
    [
        ({"runs": "not a list"}, "non-array 'runs'"),
        ({"runs": ["not an object"]}, "non-object entry"),
        ({"runs": [{"results": "not a list"}]}, "non-array 'results'"),
        ("not a document", "not a JSON object"),
    ],
)
def test_a_malformed_sarif_is_an_error_not_zero_results(payload, match):
    with pytest.raises(QualityInputError, match=match):
        count_sarif_results(payload)


def test_finding_lines_skip_a_non_object_run():
    sarif = {"runs": ["junk", {"results": [_result("b.py", 3, "R1")]}]}
    assert read_finding_lines(sarif) == ("b.py:3 [R1]",)  # nosec B101


@pytest.mark.parametrize(
    "result",
    [
        {"locations": [{"physicalLocation": "not an object"}]},
        {"locations": [{"physicalLocation": {"artifactLocation": "nope"}}]},
        {"locations": ["not an object"]},
        {"locations": [{}]},
    ],
)
def test_finding_lines_degrade_on_every_broken_location_shape(result):
    assert read_finding_lines({"runs": [{"results": [result]}]}) == ("(no location)",)  # nosec B101


def test_a_uri_without_a_region_still_names_the_file():
    result = {
        "ruleId": "R",
        "locations": [{"physicalLocation": {"artifactLocation": {"uri": "x"}}}],
    }
    assert read_finding_lines({"runs": [{"results": [result]}]}) == ("x [R]",)  # nosec B101


# ── a scan that never completed ─────────────────────────────────────────────


def test_a_broken_scan_is_exit_two_and_reads_no_counts():
    decision = broken_decision("error")
    assert decision.broken is True  # nosec B101
    assert decision.exit_code == 2  # nosec B101
    assert decision.failed is False  # nosec B101
    assert decision.gated is False  # nosec B101
    assert decision.reason == "broken"  # nosec B101
    # The threshold is still echoed, so the summary and the outputs can name it.
    assert decision.fail_on == "error"  # nosec B101


def test_a_broken_scan_is_not_the_same_as_a_clean_one():
    # Both have zero net-new findings. Only one of them is a passing PR — which is the
    # whole reason `chargate ci` writing its counts before it checks for runs matters.
    clean = decide_quality_gate(parse_counts(_counts(net_new=0, total=0, levels={})), "any")
    assert clean.exit_code == 0  # nosec B101
    assert broken_decision("any").exit_code == 2  # nosec B101


# ── a completed scan is not necessarily a full one ──────────────────────────


def test_a_scan_note_rides_along_and_never_gates():
    # chargate exits 0 having declined to start a linter it has no image for. The
    # findings that linter would have reported are simply absent, and absent findings
    # are what a clean repo looks like — so the shortfall is stated, not gated on.
    counts = parse_counts(_counts(net_new=0, total=0, levels={}))
    decision = decide_quality_gate(counts, "any", scan_note="JAVA_PMD (no image)")
    assert decision.scan_note == "JAVA_PMD (no image)"  # nosec B101
    assert decision.failed is False  # nosec B101
    assert decision.exit_code == 0  # nosec B101


def test_an_empty_scan_note_is_normalised_away():
    decision = decide_quality_gate(parse_counts(_counts()), scan_note="   ")
    assert decision.scan_note == ""  # nosec B101
