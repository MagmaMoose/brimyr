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
from fnmatch import fnmatch

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
    # Changed files matching any of these globs are dropped from the DENOMINATOR
    # entirely — they are not counted as covered, they simply do not count.
    #
    # This exists for GENERATED CODE, which is the common reason a real patch-coverage
    # number is unusable: EF Core migrations, *ModelSnapshot, *.Designer.cs, generated
    # Razor documents, protobuf/OpenAPI stubs. Nobody writes tests for them, they can be
    # thousands of lines, and one scaffolded migration in a PR can sink an otherwise
    # well-tested change below the threshold. Coverage tools have the same idea under
    # different names (coverlet's `Exclude`, ReportGenerator's `-classfilters`).
    #
    # Matched against the repo-relative, forward-slash diff path, with `fnmatch`
    # semantics where `*` also crosses `/` — so `*Migrations*` catches the folder at any
    # depth, which is how people actually write these.
    exclude_globs: tuple[str, ...] = ()


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


def _excluded(path: str, globs: tuple[str, ...]) -> bool:
    """True when a changed file should not count toward patch coverage at all.

    `fnmatch` rather than `Path.match`: `*` has to cross `/` so that `*Migrations*`
    matches `src/Api/Migrations/0001_Init.cs` without the caller having to know how deep
    the folder sits. Path.match anchors on components and would miss it.
    """
    if not globs:
        return False
    target = normalize_path(path)
    return any(fnmatch(target, pattern) for pattern in globs)


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
        if _excluded(file_diff.path, policy.exclude_globs):
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


@dataclass(frozen=True)
class TotalCoverage:
    """Overall coverage of the files a run **measured** — not of the repository.

    The distinction is load-bearing and is why this is not called "project coverage".
    A coverage report only mentions files the test run actually loaded, so a module no
    test imports is absent from the report and therefore absent from this denominator.
    A brand-new, wholly untested file can *raise* this number rather than lower it.
    Say "coverage of what we measured", never "coverage of the codebase".

    It also will not equal SonarQube's number, which applies its own
    ``sonar.coverage.exclusions`` and source scoping. Two figures that disagree are
    worse than one, so this is for the trend inside a PR comment; SonarQube owns the
    authoritative long-run total.
    """

    covered_lines: int
    executable_lines: int
    files: int

    @property
    def measured(self) -> bool:
        return self.executable_lines > 0

    @property
    def percent(self) -> float | None:
        """Coverage percentage, or ``None`` when nothing executable was measured.

        Deliberately **not** the vacuous 100.0 that :class:`PatchCoverage` returns on an
        empty denominator. That convention exists because a docs-only PR genuinely has
        nothing to cover and should pass; here an empty denominator means the run
        measured nothing at all, and reporting that as "100%" would be a lie that
        happens to look like good news.
        """
        if not self.measured:
            return None
        return 100.0 * self.covered_lines / self.executable_lines


def _canonical_buckets(report: CoverageReport) -> list[tuple[list[str], dict[int, bool]]]:
    """Fold report entries that are the same file spelled differently.

    :func:`brimyr.coverage.model.merge_reports` folds by exact path string, which is
    right for merging but leaves the same file present twice when two reports root it
    differently — coverlet emitting ``/home/runner/work/r/r/src/A.cs`` from one test
    project and ``src/A.cs`` from another. Patch coverage never notices, because
    :func:`_match` resolves one diff path to one entry; a naive sum over
    ``report.files`` does notice, and counts the file's lines twice. Measured, that
    turns a fully covered shared file into 50%.

    Entries are grouped by basename first so this stays linear in practice — only files
    sharing a name are ever compared — and folded covered-wins, matching
    :class:`CoverageBuilder`.
    """
    by_basename: dict[str, list[tuple[list[str], dict[int, bool]]]] = {}
    for file_cov in report.files:
        basename = file_cov.path.rpartition("/")[2]
        lines = dict.fromkeys(file_cov.covered, True)
        lines.update(dict.fromkeys(file_cov.uncovered, False))
        for paths, merged in by_basename.setdefault(basename, []):
            if any(_same_file(file_cov.path, known) for known in paths):
                paths.append(file_cov.path)
                for line, hit in lines.items():
                    merged[line] = merged.get(line, False) or hit
                break
        else:
            by_basename[basename].append(([file_cov.path], lines))
    return [bucket for buckets in by_basename.values() for bucket in buckets]


def _same_file(a: str, b: str) -> bool:
    """True when two coverage paths denote the same file, one being rooted deeper."""
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return a == b or ("/" in short and long_.endswith("/" + short))


def compute_total_coverage(
    report: CoverageReport,
    policy: PatchPolicy | None = None,
) -> TotalCoverage:
    """Overall coverage across everything ``report`` measured.

    Reported alongside the patch number and never gated on — the same split chargate
    uses, where the build blocks on net-new findings but the full picture still ships.

    ``policy.exclude_globs`` is applied here too. It has to be: those globs exist to
    drop generated code from the patch denominator, and a total that silently
    re-included every EF migration would disagree with the patch number by twenty
    points in the same PR comment and read as a bug. A bucket is dropped if *any* of
    its spellings matches, so a prefix-rooted path cannot smuggle a migration back in.
    """
    policy = policy or PatchPolicy()
    covered = 0
    executable = 0
    files = 0
    for paths, lines in _canonical_buckets(report):
        if any(_excluded(path, policy.exclude_globs) for path in paths):
            continue
        files += 1
        executable += len(lines)
        covered += sum(1 for hit in lines.values() if hit)
    return TotalCoverage(covered_lines=covered, executable_lines=executable, files=files)
