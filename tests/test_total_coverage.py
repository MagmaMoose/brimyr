"""Total coverage reported alongside the patch gate — and the ways it goes quietly wrong.

The chargate split: the build blocks on net-new findings, but the full picture still
ships. Here the gate stays patch-only and a total is reported next to it.

A second number in the same PR comment is only worth having if it agrees with the first
one's rules. Each test below pins a specific way a naive `sum(report.files)` produces a
plausible-looking wrong figure — plausible being the problem, since nobody double-checks
a number that looks reasonable.
"""

from __future__ import annotations

from brimyr.coverage.diff import DiffIndex, FileDiff
from brimyr.coverage.model import CoverageReport, FileCoverage, merge_reports
from brimyr.coverage.patch import (
    PatchPolicy,
    compute_patch_coverage,
    compute_total_coverage,
)
from brimyr.gate import decide_gate


def _file(path, covered=(), uncovered=()):
    return FileCoverage(path, frozenset(covered), frozenset(uncovered))


class TestTheNumber:
    def test_counts_every_measured_line(self):
        report = CoverageReport((_file("a.py", [1, 2], [3]), _file("b.py", [1], [2])))
        total = compute_total_coverage(report)
        assert total.covered_lines == 3  # nosec B101
        assert total.executable_lines == 5  # nosec B101
        assert total.percent == 60.0  # nosec B101
        assert total.files == 2  # nosec B101

    def test_nothing_measured_is_none_not_a_hundred(self):
        """An empty denominator here means "we measured nothing", not "all covered".

        PatchCoverage returns a vacuous 100.0% because a docs-only PR genuinely has
        nothing to cover and should pass. Reusing that convention for a total would
        report a run that measured zero lines as perfect.
        """
        total = compute_total_coverage(CoverageReport(()))
        assert total.percent is None  # nosec B101
        assert total.measured is False  # nosec B101


class TestSamePathTwice:
    """The trap that is live on exactly the multi-project .NET solutions we target."""

    def test_one_file_rooted_two_ways_is_not_counted_twice(self):
        # Two test projects in one solution, each rooting the shared file differently.
        # merge_reports folds by exact path string, so both survive into the report.
        covered = CoverageReport((_file("/home/runner/work/r/r/src/A.cs", [1, 2]),))
        uncovered = CoverageReport((_file("src/A.cs", [], [1, 2]),))
        merged = merge_reports([covered, uncovered])
        assert len(merged.files) == 2  # nosec B101 - the two spellings both survive

        total = compute_total_coverage(merged)
        # A naive sum reports 2/4 = 50% for a file that is fully covered.
        assert total.files == 1  # nosec B101
        assert total.percent == 100.0  # nosec B101

    def test_same_basename_different_files_stay_separate(self):
        """Folding must key on the path, not the filename.

        Every .NET project has a Program.cs; collapsing them by basename would merge
        unrelated files and cover one project's lines with another's.
        """
        report = CoverageReport(
            (_file("src/Api/Program.cs", [1]), _file("src/Worker/Program.cs", [], [1]))
        )
        total = compute_total_coverage(report)
        assert total.files == 2  # nosec B101
        assert total.percent == 50.0  # nosec B101

    def test_folding_is_covered_wins(self):
        report = merge_reports(
            [
                CoverageReport((_file("/abs/src/A.cs", [1], [2]),)),
                CoverageReport((_file("src/A.cs", [2], [1]),)),
            ]
        )
        total = compute_total_coverage(report)
        assert total.percent == 100.0  # nosec B101


class TestExclusions:
    def test_excluded_files_leave_the_total_too(self):
        """Both numbers must obey the same globs.

        If they don't, a PR comment shows a patch figure that ignored the migrations and
        a total that didn't, disagreeing by twenty points — which reads as a bug in the
        tool rather than as two different metrics.
        """
        report = CoverageReport(
            (
                _file("src/Api/Migrations/0001_Init.cs", [], list(range(1, 101))),
                _file("src/Api/Service.cs", [1, 2, 3], [4]),
            )
        )
        assert compute_total_coverage(report).percent < 5.0  # nosec B101
        policy = PatchPolicy(exclude_globs=("*Migrations*",))
        assert compute_total_coverage(report, policy).percent == 75.0  # nosec B101

    def test_a_prefixed_spelling_cannot_smuggle_an_exclusion_back_in(self):
        """A bucket is dropped if ANY of its spellings matches the glob."""
        report = merge_reports(
            [
                CoverageReport((_file("/home/runner/work/r/r/Migrations/M.cs", [], [1, 2]),)),
                CoverageReport((_file("Migrations/M.cs", [], [1, 2]),)),
            ]
        )
        policy = PatchPolicy(exclude_globs=("*Migrations*",))
        assert compute_total_coverage(report, policy).measured is False  # nosec B101


class TestItNeverGates:
    def test_a_terrible_total_does_not_fail_the_gate(self):
        """Reported, never gated on — the whole point of the split."""
        report = CoverageReport(
            (_file("a.py", [4], []), _file("legacy.py", [], list(range(1, 999))))
        )
        diff = DiffIndex((FileDiff(path="a.py", status="modified", added_ranges=((4, 4),)),))
        patch = compute_patch_coverage(diff, report)
        total = compute_total_coverage(report)

        decision = decide_gate(patch, 80.0, total=total)
        assert total.percent < 1.0  # nosec B101 - the codebase is a wasteland
        assert decision.patch.percent == 100.0  # nosec B101 - but this PR is tested
        assert decision.failed is False  # nosec B101
        assert decision.exit_code == 0  # nosec B101

    def test_total_is_optional_so_existing_callers_still_work(self):
        patch = compute_patch_coverage(DiffIndex(()), CoverageReport(()))
        assert decide_gate(patch, 80.0).total is None  # nosec B101
