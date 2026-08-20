"""Unit tests for the JaCoCo parser (brimyr.coverage.jacoco)."""

from __future__ import annotations

import pytest

from brimyr.coverage.jacoco import JacocoError, is_jacoco, parse_jacoco

# A realistic report head: JaCoCo always emits the XML declaration and a DOCTYPE
# referencing an external DTD. ElementTree must skip it without trying to fetch it.
JACOCO = """\
<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<!DOCTYPE report PUBLIC "-//JACOCO//DTD Report 1.1//EN" "report.dtd">
<report name="isam3d-case">
  <sessioninfo id="runner-1" start="1" dump="2"/>
  <package name="nl/example/isam/case">
    <class name="nl/example/isam/case/CaseService" sourcefilename="CaseService.java">
      <method name="find" desc="()V" line="12">
        <counter type="LINE" missed="0" covered="1"/>
      </method>
    </class>
    <sourcefile name="CaseService.java">
      <line nr="12" mi="0" ci="4" mb="0" cb="0"/>
      <line nr="13" mi="3" ci="0" mb="0" cb="0"/>
      <line nr="14" mi="2" ci="6" mb="1" cb="1"/>
      <counter type="LINE" missed="1" covered="2"/>
    </sourcefile>
  </package>
</report>
"""


def test_joins_package_and_sourcefile_into_a_path():
    report = parse_jacoco(JACOCO)
    assert report.get("nl/example/isam/case/CaseService.java") is not None  # nosec B101


def test_covered_instructions_decide_coverage():
    service = parse_jacoco(JACOCO).get("nl/example/isam/case/CaseService.java")
    assert service is not None  # nosec B101
    assert 12 in service.covered  # nosec B101
    assert 13 in service.uncovered  # nosec B101


def test_partially_covered_line_counts_as_covered():
    """`ci>0 and mi>0` is a short-circuited boolean, not an uncovered line.

    JaCoCo's own LINE counter calls it covered and so does diff-cover; treating it
    as missed would penalise every `a && b` in the diff.
    """
    service = parse_jacoco(JACOCO).get("nl/example/isam/case/CaseService.java")
    assert service is not None  # nosec B101
    assert 14 in service.covered  # nosec B101


def test_method_line_elements_do_not_leak_into_the_report():
    """Only `<sourcefile>` lines are executable-line facts.

    `<class>` carries `<method>` children with their own `line=` attributes, and
    `root.iter("line")` from the wrong level would invent coverage for them.
    """
    service = parse_jacoco(JACOCO).get("nl/example/isam/case/CaseService.java")
    assert service is not None  # nosec B101
    assert service.executable == {12, 13, 14}  # nosec B101


def test_default_package_yields_a_bare_filename():
    report = parse_jacoco(
        '<report name="r"><package name="">'
        '<sourcefile name="Main.java"><line nr="1" mi="0" ci="1"/></sourcefile>'
        "</package></report>"
    )
    assert report.get("Main.java") is not None  # nosec B101


def test_multi_module_reactor_keeps_modules_separate():
    """Each module writes its own report; two files must not fuse."""
    report = parse_jacoco(
        '<report name="reactor">'
        '<package name="nl/a"><sourcefile name="A.java">'
        '<line nr="1" mi="0" ci="1"/></sourcefile></package>'
        '<package name="nl/b"><sourcefile name="B.java">'
        '<line nr="1" mi="1" ci="0"/></sourcefile></package>'
        "</report>"
    )
    a = report.get("nl/a/A.java")
    b = report.get("nl/b/B.java")
    assert a is not None and b is not None  # nosec B101
    assert a.covered == {1}  # nosec B101
    assert b.uncovered == {1}  # nosec B101


def test_malformed_xml_raises():
    with pytest.raises(JacocoError):
        parse_jacoco("<report><package>")


def test_unparseable_input_is_an_error_not_an_empty_report():
    """The broken-run rule: garbage must raise, never parse to 0 files."""
    with pytest.raises(JacocoError):
        parse_jacoco("this is not xml")


def test_missing_line_number_is_skipped():
    report = parse_jacoco(
        '<report><package name="p"><sourcefile name="S.java">'
        '<line mi="0" ci="1"/><line nr="5" mi="0" ci="1"/>'
        "</sourcefile></package></report>"
    )
    s = report.get("p/S.java")
    assert s is not None  # nosec B101
    assert s.executable == {5}  # nosec B101


def test_non_numeric_attributes_are_skipped_not_fatal():
    report = parse_jacoco(
        '<report><package name="p"><sourcefile name="S.java">'
        '<line nr="oops" ci="1"/><line nr="7" ci="x"/><line nr="8" ci="2"/>'
        "</sourcefile></package></report>"
    )
    s = report.get("p/S.java")
    assert s is not None  # nosec B101
    assert s.executable == {8}  # nosec B101


class TestIsJacoco:
    def test_detects_jacoco_by_root_element(self):
        assert is_jacoco(JACOCO)  # nosec B101

    def test_rejects_cobertura(self):
        assert not is_jacoco('<?xml version="1.0" ?><coverage><packages/></coverage>')  # nosec B101

    def test_rejects_non_xml(self):
        assert not is_jacoco("TN:\nSF:a.js\nDA:1,1\nend_of_record\n")  # nosec B101

    def test_works_on_a_truncated_head(self):
        """The caller sniffs only the first few KB of a multi-megabyte report.

        The cut lands mid-document, long after the root element: the declaration
        plus JaCoCo's DOCTYPE alone are ~135 bytes, which is why the head buffer is
        sized in KB and not in bytes.
        """
        head = JACOCO[:200]
        assert "</report>" not in head  # nosec B101
        assert is_jacoco(head)  # nosec B101
