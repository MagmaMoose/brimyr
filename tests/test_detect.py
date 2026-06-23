"""Unit tests for ecosystem detection (brimyr.detect)."""

from __future__ import annotations

from brimyr.detect import (
    CoverageFormat,
    detect_ecosystems,
    ecosystem,
    locate_coverage_file,
)


def test_detect_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["python"]
    assert found[0].coverage_format is CoverageFormat.COBERTURA


def test_detect_javascript(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["javascript"]
    assert found[0].coverage_format is CoverageFormat.LCOV


def test_detect_dotnet_by_glob(tmp_path):
    (tmp_path / "App.csproj").write_text("<Project/>")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["dotnet"]


def test_detect_polyglot(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "package.json").write_text("{}")
    found = detect_ecosystems(tmp_path)
    assert {e.key for e in found} == {"python", "javascript"}


def test_detect_none(tmp_path):
    assert detect_ecosystems(tmp_path) == []


def test_ecosystem_lookup():
    assert ecosystem("python").key == "python"
    assert ecosystem("PYTHON").key == "python"
    assert ecosystem("nope") is None


def test_locate_coverage_exact(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    eco = ecosystem("python")
    assert locate_coverage_file(eco, tmp_path).name == "coverage.xml"


def test_locate_coverage_glob(tmp_path):
    eco = ecosystem("dotnet")
    nested = tmp_path / "TestResults" / "guid-123"
    nested.mkdir(parents=True)
    (nested / "coverage.cobertura.xml").write_text("<coverage/>")
    found = locate_coverage_file(eco, tmp_path)
    assert found is not None
    assert found.name == "coverage.cobertura.xml"


def test_locate_coverage_missing(tmp_path):
    assert locate_coverage_file(ecosystem("python"), tmp_path) is None
