"""Unit tests for the Cobertura parser (brimyr.coverage.cobertura)."""

from __future__ import annotations

import pytest

from brimyr.coverage.cobertura import CoberturaError, parse_cobertura

COBERTURA = """\
<?xml version="1.0" ?>
<coverage version="1.0">
  <sources><source>/repo</source></sources>
  <packages>
    <package name="pkg">
      <classes>
        <class filename="pkg/mod.py" name="mod">
          <methods>
            <method name="f"><lines><line number="2" hits="1"/></lines></method>
          </methods>
          <lines>
            <line number="1" hits="1"/>
            <line number="2" hits="1"/>
            <line number="3" hits="0"/>
          </lines>
        </class>
        <class filename="pkg/other.py" name="other">
          <lines>
            <line number="7" hits="0"/>
          </lines>
        </class>
      </classes>
    </package>
  </packages>
</coverage>
"""


def test_parses_class_lines():
    report = parse_cobertura(COBERTURA)
    mod = report.get("pkg/mod.py")
    assert mod is not None
    assert mod.covered == frozenset({1, 2})
    assert mod.uncovered == frozenset({3})


def test_method_lines_do_not_break_class_lines():
    # The <method> line 2 (hits=1) is consistent with the class line 2.
    report = parse_cobertura(COBERTURA)
    assert report.get("pkg/mod.py").is_covered(2)


def test_zero_hit_file():
    report = parse_cobertura(COBERTURA)
    other = report.get("pkg/other.py")
    assert other.covered == frozenset()
    assert other.uncovered == frozenset({7})


def test_invalid_xml_raises():
    with pytest.raises(CoberturaError):
        parse_cobertura("<coverage><not-closed>")


def test_class_without_filename_skipped():
    xml = (
        "<coverage><packages><package><classes><class><lines>"
        '<line number="1" hits="1"/></lines></class></classes></package></packages></coverage>'
    )
    assert len(parse_cobertura(xml)) == 0
