"""Unit tests for patch-coverage computation (brimyr.coverage.patch)."""

from __future__ import annotations

import pytest

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.patch import PatchPolicy, compute_patch_coverage


def _diff(*files: FileDiff) -> DiffIndex:
    return DiffIndex(tuple(files))


def test_basic_patch_coverage(make_report):
    # Changed lines 1-4; coverage says 1,2 covered, 3 uncovered, 4 is a blank
    # (not in the report) so it is excluded from the denominator.
    diff = _diff(FileDiff("a.py", "modified", ((1, 4),)))
    report = make_report({"a.py": {1: 1, 2: 1, 3: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 3  # nosec B101 - lines 1,2,3 are executable; 4 excluded
    assert patch.covered_lines == 2  # nosec B101
    assert round(patch.percent, 1) == 66.7  # nosec B101
    assert patch.files[0].missing_lines == (3,)  # nosec B101


def test_vacuous_pass_when_nothing_coverable(make_report):
    # A docs-only change: the changed file isn't in the coverage report at all.
    diff = _diff(FileDiff("README.md", "modified", ((1, 10),)))
    report = make_report({"a.py": {1: 1}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 0  # nosec B101
    assert not patch.has_measurable  # nosec B101
    assert patch.percent == 100.0  # nosec B101


def test_new_file_all_lines_count(make_report):
    diff = _diff(FileDiff("a.py", "added", ((1, 3),)))
    report = make_report({"a.py": {1: 1, 2: 0, 3: 1}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 3  # nosec B101
    assert patch.covered_lines == 2  # nosec B101


def test_pre_existing_uncovered_lines_excluded(make_report):
    # File has uncovered lines elsewhere (50), but the PR only changed line 1.
    diff = _diff(FileDiff("a.py", "modified", ((1, 1),)))
    report = make_report({"a.py": {1: 1, 50: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 1  # nosec B101
    assert patch.covered_lines == 1  # nosec B101
    assert patch.percent == 100.0  # nosec B101


def test_deleted_file_ignored(make_report):
    diff = _diff(FileDiff("gone.py", "deleted", ()))
    report = make_report({"a.py": {1: 1}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 0  # nosec B101


def test_absolute_coverage_path_suffix_match(make_report):
    diff = _diff(FileDiff("src/a.py", "modified", ((1, 2),)))
    report = make_report({"/runner/work/repo/src/a.py": {1: 1, 2: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 2  # nosec B101
    assert patch.covered_lines == 1  # nosec B101


def test_strip_prefix_match(make_report):
    diff = _diff(FileDiff("a.py", "modified", ((1, 1),)))
    report = make_report({"backend/a.py": {1: 1}})
    policy = PatchPolicy(strip_prefixes=("backend/",))
    patch = compute_patch_coverage(diff, report, policy)
    assert patch.total_lines == 1  # nosec B101
    assert patch.covered_lines == 1  # nosec B101


def test_multi_file_aggregation(make_report):
    diff = _diff(
        FileDiff("a.py", "modified", ((1, 2),)),
        FileDiff("b.py", "added", ((1, 2),)),
    )
    report = make_report({"a.py": {1: 1, 2: 1}, "b.py": {1: 0, 2: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 4  # nosec B101
    assert patch.covered_lines == 2  # nosec B101
    assert patch.percent == 50.0  # nosec B101
    below = patch.files_below(80.0)
    assert {f.path for f in below} == {"b.py"}  # nosec B101


def test_suffix_match_can_be_disabled(make_report):
    diff = _diff(FileDiff("src/a.py", "modified", ((1, 1),)))
    report = make_report({"/abs/src/a.py": {1: 1}})
    patch = compute_patch_coverage(diff, report, PatchPolicy(suffix_match=False))
    assert patch.total_lines == 0  # nosec B101 - no exact match, suffix disabled


# ── exclude_globs: generated code must not sink an otherwise well-tested change ──────


def _diff_of(*paths: str) -> DiffIndex:
    return DiffIndex(tuple(FileDiff(p, "modified", ((1, 2),)) for p in paths))


def test_excluded_files_leave_the_denominator_entirely(make_report):
    """Not counted as covered — not counted at all."""
    report = make_report(
        {
            "src/Api/Handler.cs": {1: 1, 2: 1},
            "src/Api/Migrations/0001_Init.cs": {1: 0, 2: 0},
        }
    )
    diff = _diff_of("src/Api/Handler.cs", "src/Api/Migrations/0001_Init.cs")

    without = compute_patch_coverage(diff, report)
    with_exclude = compute_patch_coverage(
        diff, report, PatchPolicy(exclude_globs=("*Migrations*",))
    )

    assert without.total_lines == 4 and without.percent == 50.0  # nosec B101
    assert with_exclude.total_lines == 2, "the migration must not be in the denominator"  # nosec B101
    assert with_exclude.percent == 100.0  # nosec B101


def test_glob_crosses_directory_separators(make_report):
    """`*Migrations*` has to match at any depth — Path.match would not."""
    report = make_report({"a/b/c/Migrations/X.cs": {1: 0, 2: 0}})
    patch = compute_patch_coverage(
        _diff_of("a/b/c/Migrations/X.cs"), report, PatchPolicy(exclude_globs=("*Migrations*",))
    )

    assert patch.total_lines == 0  # nosec B101


def test_excluding_everything_is_a_vacuous_pass_not_a_zero(make_report):
    """Consistent with the nothing-coverable-changed rule elsewhere."""
    report = make_report({"gen/A.cs": {1: 0}})
    patch = compute_patch_coverage(
        _diff_of("gen/A.cs"), report, PatchPolicy(exclude_globs=("gen/*",))
    )

    assert patch.total_lines == 0  # nosec B101
    assert patch.percent == 100.0  # nosec B101
    assert not patch.has_measurable  # nosec B101


def test_no_globs_changes_nothing(make_report):
    report = make_report({"src/A.cs": {1: 1, 2: 0}})
    diff = _diff_of("src/A.cs")

    assert (  # nosec B101
        compute_patch_coverage(diff, report).percent
        == compute_patch_coverage(diff, report, PatchPolicy(exclude_globs=())).percent
    )


@pytest.mark.parametrize(
    "pattern",
    ["*ModelSnapshot*", "*.Designer.cs", "*AspNetCoreGeneratedDocument*", "**/obj/**"],
)
def test_the_patterns_a_dotnet_consumer_would_actually_write(make_report, pattern):
    paths = {
        "*ModelSnapshot*": "src/Data/AppDbContextModelSnapshot.cs",
        "*.Designer.cs": "src/Ui/Form1.Designer.cs",
        "*AspNetCoreGeneratedDocument*": "obj/AspNetCoreGeneratedDocument/Views_Home.cs",
        "**/obj/**": "src/Api/obj/Debug/gen.cs",
    }
    path = paths[pattern]
    report = make_report({path: {1: 0, 2: 0}})
    patch = compute_patch_coverage(_diff_of(path), report, PatchPolicy(exclude_globs=(pattern,)))

    assert patch.total_lines == 0, f"{pattern} should have excluded {path}"  # nosec B101
