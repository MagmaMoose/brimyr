"""Multi-report ingestion: a solution with several test projects.

`dotnet test` on a solution writes one `TestResults/<guid>/coverage.cobertura.xml` PER
TEST PROJECT. Ingesting only one of them is silently wrong in the worst possible way:
the dropped projects' files are then absent from the report, and
:mod:`brimyr.coverage.patch` treats a file the report never mentions as contributing
nothing — so changed lines in those projects leave the denominator entirely and the gate
reports a comfortable pass instead of failing loudly.

These tests exist because that is invisible from the outside. A wrong number that looks
plausible is worse than a crash, so each one pins a specific way the number could go
quietly wrong.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.patch import compute_patch_coverage
from brimyr.detect import ecosystem, locate_coverage_file, locate_coverage_files
from brimyr.runner import run_one

_COBERTURA = """<?xml version="1.0"?>
<coverage><packages><package name="P"><classes>
<class filename="{path}"><lines>
{lines}
</lines></class>
</classes></package></packages></coverage>"""


def _write_report(directory: Path, source: str, hits: dict[int, int]) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    lines = "\n".join(f'<line number="{n}" hits="{h}"/>' for n, h in sorted(hits.items()))
    target = directory / "coverage.cobertura.xml"
    target.write_text(_COBERTURA.format(path=source, lines=lines))
    return target


def _solution(root: Path, projects: dict[str, dict[int, int]]) -> None:
    """A .NET solution whose `dotnet test` left one report per test project."""
    (root / "App.sln").write_text("")
    for index, (source, hits) in enumerate(projects.items()):
        _write_report(root / "TestResults" / f"guid-{index}", source, hits)


def _fake_runner(returncode: int = 0):
    def _run(command: str, cwd: str) -> subprocess.CompletedProcess:
        return subprocess.CompletedProcess(args=command, returncode=returncode)

    return _run


def test_every_test_project_report_is_located(tmp_path):
    _solution(
        tmp_path,
        {
            "src/Api/Handler.cs": {1: 1},
            "src/Core/Rules.cs": {1: 1},
            "src/Web/Page.cs": {1: 1},
        },
    )
    found = locate_coverage_files(ecosystem("dotnet"), tmp_path)

    assert len(found) == 3, "one report per test project must be located"


def test_located_order_is_stable_not_mtime(tmp_path):
    """Two runs over the same tree must merge identically.

    The previous implementation sorted by mtime and took the newest, so which project's
    coverage survived depended on which test project happened to finish last.
    """
    _solution(tmp_path, {"src/A/A.cs": {1: 1}, "src/B/B.cs": {1: 1}})
    first = locate_coverage_files(ecosystem("dotnet"), tmp_path)
    (tmp_path / "TestResults" / "guid-0" / "coverage.cobertura.xml").touch()  # newest now
    second = locate_coverage_files(ecosystem("dotnet"), tmp_path)

    assert first == second


def test_run_one_merges_all_reports(tmp_path):
    _solution(
        tmp_path,
        {"src/Api/Handler.cs": {1: 1, 2: 0}, "src/Core/Rules.cs": {5: 1}},
    )
    outcome = run_one(ecosystem("dotnet"), tmp_path, runner=_fake_runner())

    assert outcome.ok
    assert len(outcome.coverage_paths) == 2
    assert {f.path for f in outcome.report.files} == {"src/Api/Handler.cs", "src/Core/Rules.cs"}


def test_the_gate_does_not_silently_pass_a_dropped_project(tmp_path):
    """The bug this file exists for, stated as the number it produced.

    A change in a project whose report was dropped used to leave the denominator, giving
    a vacuous 100%. With every report merged it is counted and the shortfall is visible.
    """
    # ORDER MATTERS FOR THIS TEST. The old code kept the most RECENTLY written report, so
    # the uncovered project is written FIRST and is therefore the one that used to be
    # dropped. With it listed second the bug hides: the dropped report would be the
    # covered one, the changed lines would still be present, and this test would pass
    # against broken code.
    _solution(
        tmp_path,
        {
            "src/Core/Rules.cs": {1: 0, 2: 0},  # NOT covered, written first => dropped
            "src/Api/Handler.cs": {1: 1, 2: 1},  # covered, written last  => kept
        },
    )
    outcome = run_one(ecosystem("dotnet"), tmp_path, runner=_fake_runner())

    # The PR changed lines 1-2 of the uncovered project only.
    diff = DiffIndex((FileDiff("src/Core/Rules.cs", "modified", ((1, 2),)),))
    patch = compute_patch_coverage(diff, outcome.report)

    assert patch.total_lines == 2, "the changed lines must be in the denominator"
    assert patch.percent == 0.0, "an uncovered change must read 0%, not a vacuous 100%"


def test_a_single_unparseable_report_breaks_the_run(tmp_path):
    """Not a quietly smaller number — a broken run is exit 2, never 0%."""
    _solution(tmp_path, {"src/Api/Handler.cs": {1: 1}})
    _write_report(tmp_path / "TestResults" / "guid-9", "x", {})
    (tmp_path / "TestResults" / "guid-9" / "coverage.cobertura.xml").write_text("not xml")

    outcome = run_one(ecosystem("dotnet"), tmp_path, runner=_fake_runner())

    assert not outcome.ok
    assert outcome.report is None
    assert outcome.error


def test_single_report_ecosystems_are_unaffected(tmp_path):
    """pytest writes one coverage.xml; nothing about that changes."""
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "coverage.xml").write_text(_COBERTURA.format(path="app.py", lines=""))

    found = locate_coverage_files(ecosystem("python"), tmp_path)

    assert [p.name for p in found] == ["coverage.xml"]
    assert locate_coverage_file(ecosystem("python"), tmp_path).name == "coverage.xml"


@pytest.mark.parametrize("count", [1, 2, 12])
def test_scales_to_however_many_projects_a_solution_has(tmp_path, count):
    _solution(tmp_path, {f"src/P{i}/File{i}.cs": {1: 1} for i in range(count)})
    outcome = run_one(ecosystem("dotnet"), tmp_path, runner=_fake_runner())

    assert len(outcome.coverage_paths) == count
    assert len(outcome.report.files) == count
