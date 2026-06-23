"""Patch coverage: the fraction of *changed executable lines* the tests covered.

This is the gate's heart, and it is **pure**. Given a :class:`DiffIndex` (the
lines a PR added/modified) and a :class:`CoverageReport` (executable lines the run
covered), it intersects them per file:

* **denominator** — changed lines that the coverage tool considers *executable*
  (changed-and-coverable). Blank lines, comments and braces are excluded because
  they never appear in a coverage report; files the report doesn't mention at all
  (a doc, a config, a brand-new untested file the suite never imported) contribute
  nothing — exactly diff-cover's behaviour.
* **numerator** — those changed-executable lines that were *covered*.

``percent = 100 · covered / total``, or **100% when nothing coverable changed**
(a docs-only PR vacuously passes; there is nothing to cover).

Coverage-report paths and ``git diff`` paths rarely match byte-for-byte (absolute
vs repo-relative, ``<source>`` roots, monorepo prefixes), so matching falls back
from exact to suffix matching. Pass extra ``strip_prefixes`` to peel known roots.
"""

from __future__ import annotations

from dataclasses import dataclass

from brimyr.coverage.diff import DiffIndex, normalize_path
from brimyr.coverage.model import CoverageReport, FileCoverage


@dataclass(frozen=True)
class PatchPolicy:
    """How patch coverage matches coverage paths to diff paths."""

    # Path prefixes (e.g. a monorepo subdir or a Cobertura <source> root) to peel
    # off coverage-report paths before matching them against repo-relative diffs.
    strip_prefixes: tuple[str, ...] = ()
    # When exact match fails, allow matching a coverage path that is a path-suffix
    # of the diff path (or vice versa). Handles absolute coverage paths.
    suffix_match: bool = True


@dataclass(frozen=True)
class PatchFileResult:
    """Per-file patch-coverage outcome (only files with coverable changes)."""

    path: str
    covered_lines: tuple[int, ...]
    missing_lines: tuple[int, ...]

    @property
    def total(self) -> int:
        return len(self.covered_lines) + len(self.missing_lines)

    @property
    def covered(self) -> int:
        return len(self.covered_lines)

    @property
    def percent(self) -> float:
        return 100.0 if self.total == 0 else 100.0 * self.covered / self.total


@dataclass(frozen=True)
class PatchCoverage:
    """Aggregate patch coverage over a diff."""

    files: tuple[PatchFileResult, ...]
    total_lines: int
    covered_lines: int

    @property
    def missing_lines(self) -> int:
        return self.total_lines - self.covered_lines

    @property
    def has_measurable(self) -> bool:
        """Whether any changed line was executable (the denominator is non-zero)."""
        return self.total_lines > 0

    @property
    def percent(self) -> float:
        """Patch coverage %. 100.0 when nothing coverable changed (vacuous pass)."""
        if self.total_lines == 0:
            return 100.0
        return 100.0 * self.covered_lines / self.total_lines

    def files_below(self, threshold: float) -> tuple[PatchFileResult, ...]:
        """Files whose own patch coverage is under ``threshold`` (for reporting)."""
        return tuple(f for f in self.files if f.total > 0 and f.percent < threshold)


def _index(
    report: CoverageReport, strip_prefixes: tuple[str, ...]
) -> list[tuple[str, FileCoverage]]:
    """Build (lookup-key, FileCoverage) pairs, including prefix-stripped keys."""
    normed_prefixes = [normalize_path(p).rstrip("/") + "/" for p in strip_prefixes if p]
    entries: list[tuple[str, FileCoverage]] = []
    for file_cov in report.files:
        keys = {file_cov.path}
        for prefix in normed_prefixes:
            if file_cov.path.startswith(prefix):
                keys.add(file_cov.path[len(prefix) :])
        for key in keys:
            entries.append((key, file_cov))
    return entries


def _match(
    diff_path: str,
    entries: list[tuple[str, FileCoverage]],
    *,
    suffix_match: bool,
) -> FileCoverage | None:
    """Find the coverage entry for a diff path: exact, then suffix either way."""
    target = normalize_path(diff_path)
    for key, file_cov in entries:
        if key == target:
            return file_cov
    if not suffix_match:
        return None
    # Coverage path is longer (absolute / rooted): pick the shortest such match.
    best: FileCoverage | None = None
    best_len: int | None = None
    for key, file_cov in entries:
        if key.endswith("/" + target) and (best_len is None or len(key) < best_len):
            best, best_len = file_cov, len(key)
    if best is not None:
        return best
    # Coverage path is shorter (a trailing relative form): pick the longest match.
    best, best_len = None, None
    for key, file_cov in entries:
        if target.endswith("/" + key) and (best_len is None or len(key) > best_len):
            best, best_len = file_cov, len(key)
    return best


def compute_patch_coverage(
    diff: DiffIndex,
    report: CoverageReport,
    policy: PatchPolicy | None = None,
) -> PatchCoverage:
    """Compute patch coverage of ``diff`` against ``report``."""
    policy = policy or PatchPolicy()
    entries = _index(report, policy.strip_prefixes)

    file_results: list[PatchFileResult] = []
    total = 0
    covered_total = 0
    for file_diff in diff.files:
        if file_diff.is_deleted:
            continue
        file_cov = _match(file_diff.path, entries, suffix_match=policy.suffix_match)
        if file_cov is None:
            continue
        changed = file_diff.added_lines()
        coverable = changed & file_cov.executable
        if not coverable:
            continue
        covered = coverable & file_cov.covered
        missing = coverable - covered
        total += len(coverable)
        covered_total += len(covered)
        file_results.append(
            PatchFileResult(
                path=file_diff.path,
                covered_lines=tuple(sorted(covered)),
                missing_lines=tuple(sorted(missing)),
            )
        )

    return PatchCoverage(
        files=tuple(file_results),
        total_lines=total,
        covered_lines=covered_total,
    )
