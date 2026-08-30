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

import functools
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from brimyr.coverage.cobertura import CoberturaError, parse_cobertura
from brimyr.coverage.jacoco import JacocoError, parse_jacoco
from brimyr.coverage.lcov import parse_lcov
from brimyr.coverage.model import CoverageReport, merge_reports
from brimyr.detect import CoverageFormat, Ecosystem, locate_coverage_files

# A runner takes (command_string, cwd) and returns the completed process.
Runner = Callable[[str, str], subprocess.CompletedProcess]


class IngestError(RuntimeError):
    """A coverage file could not be read or parsed."""


#: Seconds before a test run is abandoned. A suite that has not finished in an hour is
#: hung, and without a limit it holds the runner until the job timeout (six hours by
#: default on GitHub-hosted). Across an estate that is real money and a support ticket,
#: and the symptom, a job that never ends, points at everything except the coverage gate.
#: A timeout is a BROKEN run (exit 2), never 0% coverage. Set 0 to disable.
DEFAULT_TEST_TIMEOUT = 3600


def _default_runner(
    command: str, cwd: str, timeout: float | None = DEFAULT_TEST_TIMEOUT
) -> subprocess.CompletedProcess:
    # shell=True is the point: `command` is the repo's OWN test command, a shell string
    # the consumer supplies (`test_command`) or that detect.py chose. There is no argv to
    # split it into. Two markers, on two different lines, because bandit anchors the
    # finding on the CALL and semgrep anchors it on the `shell=True` argument.
    return subprocess.run(  # nosec B602
        command,
        shell=True,  # nosemgrep - deliberate, see above
        cwd=cwd,
        check=False,
        timeout=timeout or None,
    )


def parse_coverage_text(text: str, fmt: CoverageFormat) -> CoverageReport:
    """Parse coverage text in the given format into a :class:`CoverageReport`."""
    if fmt is CoverageFormat.LCOV:
        return parse_lcov(text)
    if fmt is CoverageFormat.COBERTURA:
        return parse_cobertura(text)
    if fmt is CoverageFormat.JACOCO:
        return parse_jacoco(text)
    raise IngestError(f"unsupported coverage format: {fmt}")


# Maven and Gradle source roots, in the order they are tried. JaCoCo reports a path
# relative to one of these and never says which, so the only way to recover the
# repo-relative path is to test each against the filesystem.
_JVM_SOURCE_ROOTS = (
    "src/main/java",
    "src/main/kotlin",
    "src/main/scala",
    "src/test/java",
    "src/test/kotlin",
)


def _jacoco_path_resolver(report_path: Path, repo: Path) -> Callable[[str], str]:
    """Map a JaCoCo source-root-relative path to a repo-relative one.

    JaCoCo names files as ``<package>/<sourcefile>`` with **no module prefix**, so in a
    multi-module reactor `isam3d-case` and `isam3d-user` both report their own
    `nl/example/Service.java` under that identical string. `merge_reports` keys by
    string and folds covered-wins, so the covered module's data silently answers for the
    uncovered one and a changed line there is reported as covered. Measured: 100% where
    the truth was 0%.

    The module root is recoverable from where the report was found
    (``<module>/target/site/jacoco/jacoco.xml``), and the source root by testing the
    conventional ones against disk. Anything that does not resolve is returned
    unchanged, so an unusual layout degrades to the old behaviour rather than inventing
    a path that matches nothing.
    """
    module = report_path.parent
    for marker in ("jacoco", "site", "target"):
        if module.name == marker:
            module = module.parent
    candidates: list[Path] = []
    for root in _JVM_SOURCE_ROOTS:
        candidates.append(module / root)
    cache: dict[str, str] = {}

    def resolve(rel: str) -> str:
        if rel in cache:
            return cache[rel]
        out = rel
        for base in candidates:
            if (base / rel).is_file():
                try:
                    out = (base / rel).relative_to(repo).as_posix()
                except ValueError:
                    out = (base / rel).as_posix()
                break
        cache[rel] = out
        return out

    return resolve


def ingest_file(path: str | Path, fmt: CoverageFormat, repo: str | Path = ".") -> CoverageReport:
    """Read and parse a coverage file. Raises :class:`IngestError` on failure."""
    p = Path(path)
    try:
        text = p.read_text(encoding="utf-8")
    except OSError as exc:
        raise IngestError(f"could not read coverage file {p}: {exc}") from exc
    try:
        if fmt is CoverageFormat.JACOCO:
            # Only JaCoCo needs this: its paths carry no module prefix, so two modules'
            # identically-named classes would merge into one. See _jacoco_path_resolver.
            return parse_jacoco(text, resolve_path=_jacoco_path_resolver(p, Path(repo)))
        return parse_coverage_text(text, fmt)
    except (CoberturaError, JacocoError) as exc:
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
        """A clean run: tests passed and a report with actual coverage data appeared.

        `bool(report)` and not `report is not None`: a report that parses but names ZERO
        files is not coverage, it is the coverage tool having instrumented nothing. Left
        as "ok" it produces the worst possible answer, because every changed line is then
        a line the report does not mention, the denominator is 0, and the gate returns a
        vacuous 100% over completely unmeasured code.

        The common cause on the JVM is a surefire `<argLine>` that overrides rather than
        appends `@{argLine}`, which silently detaches the JaCoCo agent.
        """
        return self.returncode == 0 and bool(self.report) and self.error is None


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


@dataclass(frozen=True)
class CommandOutcome:
    """Result of a plain command run (no coverage ingestion)."""

    command: str
    returncode: int

    @property
    def ok(self) -> bool:
        return self.returncode == 0


def run_command(
    command: Sequence[str] | str,
    repo: str | Path = ".",
    *,
    runner: Runner | None = None,
) -> CommandOutcome:
    """Run one command and report its exit status. Never raises.

    Exists for the SonarScanner-for-.NET build, which has to happen between `begin` and
    `end` and is not a test run — it produces no coverage and must not be able to fail
    the gate. Failure isolation is the caller's contract, so a missing binary comes back
    as a non-zero outcome rather than an exception.
    """
    argv = command if isinstance(command, str) else " ".join(command)
    run_fn = runner or _default_runner
    try:
        completed = run_fn(argv, str(repo))
    except OSError:
        return CommandOutcome(command=argv, returncode=127)
    return CommandOutcome(command=argv, returncode=completed.returncode)


def run_one(
    eco: Ecosystem,
    repo: str | Path = ".",
    *,
    command: str | None = None,
    runner: Runner | None = None,
    timeout: float | None = DEFAULT_TEST_TIMEOUT,
) -> RunOutcome:
    """Run a single ecosystem's tests and ingest its coverage file."""
    # Bound to the DEFAULT runner only: `Runner` is a two-argument contract and every
    # injected test runner implements it, so widening it here would break them all.
    run_fn = runner or functools.partial(_default_runner, timeout=timeout)
    repo_str = str(repo)
    cmd = command or eco.command_str()

    try:
        completed = run_fn(cmd, repo_str)
    except subprocess.TimeoutExpired:
        # A broken run, not 0% coverage: the tests never finished, so there is no
        # verdict to give. Exit 2, loudly.
        return RunOutcome(
            eco,
            124,
            (),
            None,
            error=(
                f"tests did not finish within {timeout:.0f}s and were killed. This is a "
                "broken run, not 0% coverage. Raise `test_timeout` if the suite is "
                "genuinely this slow, or set it to 0 to wait indefinitely."
            ),
        )
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
            reports.append(ingest_file(path, eco.coverage_format, repo))
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
    timeout: float | None = DEFAULT_TEST_TIMEOUT,
) -> RunResult:
    """Run each ecosystem's tests and ingest coverage. ``command`` overrides all."""
    outcomes = [
        run_one(eco, repo, command=command, runner=runner, timeout=timeout) for eco in ecosystems
    ]
    return RunResult(tuple(outcomes))
