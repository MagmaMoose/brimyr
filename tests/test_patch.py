"""Unit tests for patch-coverage computation (brimyr.coverage.patch)."""

from __future__ import annotations

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
    assert patch.total_lines == 3  # lines 1,2,3 are executable; 4 excluded
    assert patch.covered_lines == 2
    assert round(patch.percent, 1) == 66.7
    assert patch.files[0].missing_lines == (3,)


def test_vacuous_pass_when_nothing_coverable(make_report):
    # A docs-only change: the changed file isn't in the coverage report at all.
    diff = _diff(FileDiff("README.md", "modified", ((1, 10),)))
    report = make_report({"a.py": {1: 1}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 0
    assert not patch.has_measurable
    assert patch.percent == 100.0


def test_new_file_all_lines_count(make_report):
    diff = _diff(FileDiff("a.py", "added", ((1, 3),)))
    report = make_report({"a.py": {1: 1, 2: 0, 3: 1}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 3
    assert patch.covered_lines == 2


def test_pre_existing_uncovered_lines_excluded(make_report):
    # File has uncovered lines elsewhere (50), but the PR only changed line 1.
    diff = _diff(FileDiff("a.py", "modified", ((1, 1),)))
    report = make_report({"a.py": {1: 1, 50: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 1
    assert patch.covered_lines == 1
    assert patch.percent == 100.0


def test_deleted_file_ignored(make_report):
    diff = _diff(FileDiff("gone.py", "deleted", ()))
    report = make_report({"a.py": {1: 1}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 0


def test_absolute_coverage_path_suffix_match(make_report):
    diff = _diff(FileDiff("src/a.py", "modified", ((1, 2),)))
    report = make_report({"/runner/work/repo/src/a.py": {1: 1, 2: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 2
    assert patch.covered_lines == 1


def test_strip_prefix_match(make_report):
    diff = _diff(FileDiff("a.py", "modified", ((1, 1),)))
    report = make_report({"backend/a.py": {1: 1}})
    policy = PatchPolicy(strip_prefixes=("backend/",))
    patch = compute_patch_coverage(diff, report, policy)
    assert patch.total_lines == 1
    assert patch.covered_lines == 1


def test_multi_file_aggregation(make_report):
    diff = _diff(
        FileDiff("a.py", "modified", ((1, 2),)),
        FileDiff("b.py", "added", ((1, 2),)),
    )
    report = make_report({"a.py": {1: 1, 2: 1}, "b.py": {1: 0, 2: 0}})
    patch = compute_patch_coverage(diff, report)
    assert patch.total_lines == 4
    assert patch.covered_lines == 2
    assert patch.percent == 50.0
    below = patch.files_below(80.0)
    assert {f.path for f in below} == {"b.py"}


def test_suffix_match_can_be_disabled(make_report):
    diff = _diff(FileDiff("src/a.py", "modified", ((1, 1),)))
    report = make_report({"/abs/src/a.py": {1: 1}})
    patch = compute_patch_coverage(diff, report, PatchPolicy(suffix_match=False))
    assert patch.total_lines == 0  # no exact match, suffix disabled
