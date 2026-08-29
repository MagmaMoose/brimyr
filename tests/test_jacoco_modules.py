"""Multi-module JaCoCo: two modules must not answer for each other.

JaCoCo names files as `<package>/<sourcefile>` with **no module prefix**, so in a Maven
reactor `isam3d-case` and `isam3d-user` both report their own `nl/x/Service.java` under
that identical string. `merge_reports` keys by string and folds covered-wins, so the
covered module's data silently answered for the uncovered one.

That is the worst shape of bug this project has: it does not crash, it returns 100% where
the truth is 0%. These tests pin the resolution that prevents it, and the measured failure
that motivated it.
"""

from __future__ import annotations

from pathlib import Path

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.jacoco import parse_jacoco
from brimyr.coverage.model import merge_reports
from brimyr.coverage.patch import compute_patch_coverage
from brimyr.detect import CoverageFormat
from brimyr.runner import ingest_file

_REPORT = (
    '<report name="{mod}"><package name="nl/x"><sourcefile name="Service.java">'
    '<line nr="11" mi="{mi}" ci="{ci}"/></sourcefile></package></report>'
)


def _reactor(tmp_path: Path) -> Path:
    """Two modules, each with its own nl/x/Service.java and its own report."""
    for mod, covered in (("isam3d-case", True), ("isam3d-user", False)):
        src = tmp_path / mod / "src/main/java/nl/x"
        src.mkdir(parents=True)
        (src / "Service.java").write_text("class Service {}\n")
        rep = tmp_path / mod / "target/site/jacoco"
        rep.mkdir(parents=True)
        ci, mi = ("5", "0") if covered else ("0", "4")
        (rep / "jacoco.xml").write_text(_REPORT.format(mod=mod, ci=ci, mi=mi))
    return tmp_path


def test_modules_resolve_to_distinct_repo_relative_paths(tmp_path):
    repo = _reactor(tmp_path)
    paths = sorted(
        f.path
        for p in sorted(repo.glob("*/target/site/jacoco/jacoco.xml"))
        for f in ingest_file(p, CoverageFormat.JACOCO, repo).files
    )
    assert paths == [  # nosec B101
        "isam3d-case/src/main/java/nl/x/Service.java",
        "isam3d-user/src/main/java/nl/x/Service.java",
    ]


def test_the_covered_module_does_not_answer_for_the_uncovered_one(tmp_path):
    """The measured regression: this reported 100% for a line with no coverage."""
    repo = _reactor(tmp_path)
    merged = merge_reports(
        [
            ingest_file(p, CoverageFormat.JACOCO, repo)
            for p in sorted(repo.glob("*/target/site/jacoco/jacoco.xml"))
        ]
    )
    assert len(merged.files) == 2  # nosec B101 - not folded into one

    diff = DiffIndex(
        (
            FileDiff(
                path="isam3d-user/src/main/java/nl/x/Service.java",
                status="modified",
                added_ranges=((11, 11),),
            ),
        )
    )
    result = compute_patch_coverage(diff, merged)
    assert result.percent == 0.0  # nosec B101
    assert result.covered_lines == 0  # nosec B101


def test_an_unresolvable_path_is_left_alone(tmp_path):
    """An unusual layout degrades to JaCoCo's own path, never an invented one.

    Inventing `<module>/src/main/java/...` for a file that isn't there would produce a
    path matching nothing, which drops the file from the denominator: a silent pass by a
    different route.
    """
    rep = tmp_path / "target/site/jacoco"
    rep.mkdir(parents=True)
    (rep / "jacoco.xml").write_text(_REPORT.format(mod="m", ci="1", mi="0"))
    report = ingest_file(rep / "jacoco.xml", CoverageFormat.JACOCO, tmp_path)
    assert [f.path for f in report.files] == ["nl/x/Service.java"]  # nosec B101


def test_the_parser_stays_pure_without_a_resolver():
    report = parse_jacoco(_REPORT.format(mod="m", ci="1", mi="0"))
    assert [f.path for f in report.files] == ["nl/x/Service.java"]  # nosec B101
