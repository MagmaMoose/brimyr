"""Run tests with coverage on, then ingest the coverage file(s).

This is the test-runner boundary — the one place that shells out to ``pytest`` /
``jest`` / ``dotnet``. Each detected :class:`Ecosystem`'s command runs with
coverage instrumentation already on (coverage is a *byproduct of the run*), then
its emitted file is located and parsed into a pure :class:`CoverageReport`.

The crucial rule lives here: a test command that exits non-zero, or that produces
no parseable coverage, is a **broken run** — a tool error (build red), never
"0% patch coverage". :attr:`RunResult.broken` surfaces that so the CLI fails with
an error exit code instead of a misleading hard gate failure.

The subprocess is injected (``runner=``) so the orchestration is unit-tested
without a real toolchain.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from brimyr.coverage.cobertura import CoberturaError, parse_cobertura
from brimyr.coverage.lcov import parse_lcov
from brimyr.coverage.model import CoverageReport, merge_reports
from brimyr.detect import CoverageFormat, Ecosystem, locate_coverage_files

# A runner takes (command_string, cwd) and returns the completed process.
Runner = Callable[[str, str], subprocess.CompletedProcess]


class IngestError(RuntimeError):
    """A coverage file could not be read or parsed."""


def _default_runner(command: str, cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(command, shell=True, cwd=cwd, check=False)


def parse_coverage_text(text: str, fmt: CoverageFormat) -> CoverageReport:
    """Parse coverage text in the given format into a :class:`CoverageReport`."""
    if fmt is CoverageFormat.LCOV:
        return parse_lcov(text)
    if fmt is CoverageFormat.COBERTURA:
        return parse_cobertura(text)
    raise IngestError(f"unsupported coverage format: {fmt}")


def ingest_file(path: str | Path, fmt: CoverageFormat) -> CoverageReport:
    """Read and parse a coverage file. Raises :class:`IngestError` on failure."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(f"could not read coverage file {p}: {exc}") from exc
    try:
        return parse_coverage_text(text, fmt)
    except CoberturaError as exc:
        raise IngestError(str(exc)) from exc


@dataclass(frozen=True)
class RunOutcome:
    """The result of running one ecosystem's tests and ingesting its coverage."""

    ecosystem: Ecosystem
    returncode: int
    #: EVERY report this ecosystem produced. `dotnet test` on a solution writes one per
    #: test project, so this is routinely more than one; empty when the run produced none.
    coverage_paths: tuple[Path, ...]
    report: CoverageReport | None
    error: str | None = None

    @property
    def coverage_path(self) -> Path | None:
        """The first report, for callers and messages that want a single example."""
        return self.coverage_paths[0] if self.coverage_paths else None

    @property
    def ok(self) -> bool:
        """A clean run: tests passed and a parseable coverage report was produced."""
        return self.returncode == 0 and self.report is not None and self.error is None


@dataclass(frozen=True)
class RunResult:
    """Outcomes across every ecosystem that ran."""

    outcomes: tuple[RunOutcome, ...]

    @property
    def broken(self) -> bool:
        """True if any ecosystem's run failed or yielded no usable coverage."""
        return any(not o.ok for o in self.outcomes)

    @property
    def report(self) -> CoverageReport:
        """The merged coverage across all ecosystems that produced one."""
        return merge_reports(o.report for o in self.outcomes if o.report is not None)

    @property
    def coverage_paths(self) -> tuple[Path, ...]:
        """Every report across every ecosystem — what Sonar's reportPaths needs."""
        return tuple(path for o in self.outcomes for path in o.coverage_paths)


def run_one(
    eco: Ecosystem,
    repo: str | Path = ".",
    *,
    command: str | None = None,
    runner: Runner | None = None,
) -> RunOutcome:
    """Run a single ecosystem's tests and ingest its coverage file."""
    run_fn = runner or _default_runner
    repo_str = str(repo)
    cmd = command or eco.command_str()

    try:
        completed = run_fn(cmd, repo_str)
    except OSError as exc:
        return RunOutcome(eco, 127, (), None, error=f"could not launch tests: {exc}")

    coverage_files = locate_coverage_files(eco, repo)
    if not coverage_files:
        return RunOutcome(
            eco,
            completed.returncode,
            (),
            None,
            error=(
                f"no coverage file found (expected one of: {', '.join(eco.coverage_paths)}). "
                "Did the test run emit coverage?"
            ),
        )

    # ALL of them, merged. A solution with several test projects leaves one report per
    # project; ingesting only the first would drop the rest, and a dropped project's
    # files are then absent from the report entirely — which patch.py reads as "nothing
    # coverable changed" rather than as an error. Covered-wins merging is also what makes
    # a file exercised by two different test projects come out covered, not half-covered.
    paths = tuple(coverage_files)
    reports: list[CoverageReport] = []
    for path in coverage_files:
        try:
            reports.append(ingest_file(path, eco.coverage_format))
        except IngestError as exc:
            # One unparseable report is a broken run, not a quietly smaller number.
            return RunOutcome(eco, completed.returncode, paths, None, error=str(exc))

    return RunOutcome(eco, completed.returncode, paths, merge_reports(reports))


def run_tests(
    ecosystems: list[Ecosystem],
    repo: str | Path = ".",
    *,
    command: str | None = None,
    runner: Runner | None = None,
) -> RunResult:
    """Run each ecosystem's tests and ingest coverage. ``command`` overrides all."""
    outcomes = [run_one(eco, repo, command=command, runner=runner) for eco in ecosystems]
    return RunResult(tuple(outcomes))
