"""Run tests with coverage on, then ingest the coverage file(s).

This is the test-runner boundary — the one place that shells out to ``pytest`` /
``jest`` / ``dotnet``. Each detected :class:`Ecosystem`'s command runs with
coverage instrumentation already on (coverage is a *byproduct of the run*), then
its emitted file is located and parsed into a pure :class:`CoverageReport`.

Before the tests, :mod:`brimyr.provision` gets a chance to make them *runnable* —
``uv run`` / ``poetry run`` / ``npm ci``. Detecting a suite Brimyr then cannot
launch is worth nothing to the consumer: it produced ``pytest: not found``, an
empty report and a red gate on a repo whose tests were fine. Provisioning is
skipped entirely when the caller supplied an explicit ``command``, which is their
contract to keep.

The crucial rule lives here: a test command that exits non-zero, or that produces
no parseable coverage, is a **broken run** — a tool error (build red), never
"0% patch coverage". :attr:`RunResult.broken` surfaces that so the CLI fails with
an error exit code instead of a misleading hard gate failure.

With one exception, and it is a different question rather than a softening of that
one: *can* this ecosystem produce coverage at all? An ecosystem that declares
:attr:`~brimyr.detect.Ecosystem.coverage_optional` cannot be expected to — bats
measures nothing unless kcov happens to be installed — so a passing run of one is
:attr:`RunOutcome.unmeasured`: green, with nothing measured, said out loud. Every
other ecosystem instruments as it runs, so a missing report there still means the
run broke and still turns the build red.

The subprocess is injected (``runner=``) so the orchestration is unit-tested
without a real toolchain.
"""

from __future__ import annotations

import functools
import os
import posixpath
import re
import shutil
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from brimyr.coverage.cobertura import CoberturaError, parse_cobertura
from brimyr.coverage.gocover import GoCoverError, parse_gocover
from brimyr.coverage.jacoco import JacocoError, parse_jacoco
from brimyr.coverage.lcov import parse_lcov
from brimyr.coverage.model import CoverageReport, merge_reports, remap_paths
from brimyr.detect import CoverageFormat, Ecosystem, locate_coverage_files
from brimyr.provision import Provision, Which
from brimyr.provision import plan as plan_provision

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
    if fmt is CoverageFormat.GOCOVER:
        return parse_gocover(text)
    raise IngestError(f"unsupported coverage format: {fmt}")


#: Directory names a `go.mod` search skips: the go tool's own exclusions plus the usual
#: vendored trees. `vendor/` holds copies of dependencies, never this repo's modules.
_GO_MOD_SKIP = frozenset({"vendor", "node_modules", "testdata"})

#: `module example.com/m`, `module "example.com/m"`, either with a trailing comment.
_GO_MODULE = re.compile(r'^\s*module\s+"?([^\s"]+)"?\s*(?://.*)?$', re.MULTILINE)


def _go_modules(repo: Path) -> list[tuple[str, str]]:
    """``(module path, repo-relative directory)`` for every ``go.mod``, longest path first.

    Longest first so that a nested module (``example.com/m/tools``) claims its own files
    before the root module (``example.com/m``) can mistake them for a package of its own.
    """
    modules: list[tuple[str, str]] = []
    for directory, subdirs, files in os.walk(repo):
        subdirs[:] = sorted(
            d for d in subdirs if d not in _GO_MOD_SKIP and not d.startswith((".", "_"))
        )
        if "go.mod" not in files:
            continue
        try:
            text = (Path(directory) / "go.mod").read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        match = _GO_MODULE.search(text)
        if match:
            modules.append((match.group(1), Path(directory).relative_to(repo).as_posix()))
    return sorted(modules, key=lambda item: len(item[0]), reverse=True)


def _go_path_resolver(repo: Path) -> Callable[[str], str]:
    """Map a coverage profile's import path to a repo-relative file path.

    ``github.com/org/repo/internal/x/x.go`` names the file by its module path, which the
    diff never carries. The module path is whatever ``go.mod`` declares, so it is read
    rather than guessed from the remote URL. Unresolvable paths come back unchanged,
    where the suffix match in :mod:`brimyr.coverage.patch` still gets its chance.
    """
    modules = _go_modules(repo)

    def resolve(name: str) -> str:
        for module, directory in modules:
            if name.startswith(module + "/"):
                rest = name[len(module) + 1 :]
                return rest if directory == "." else f"{directory}/{rest}"
        return name

    return resolve


def _source_reader(repo: Path) -> Callable[[str], list[str] | None]:
    """Read a repo-relative (or absolute) source file as lines, ``None`` when it is absent."""

    def read(path: str) -> list[str] | None:
        target = Path(path) if Path(path).is_absolute() else repo / path
        try:
            return target.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return None

    return read


def _jacoco_path_resolver(report_path: Path, repo: Path) -> Callable[[str], str]:
    """Map a JaCoCo source-root-relative path to a repo-relative one.

    JaCoCo names files as ``<package>/<sourcefile>`` with **no module prefix**, so in a
    multi-module build `isam3d-case` and `isam3d-user` both report their own
    `nl/example/Service.java` under that identical string. `merge_reports` keys by string
    and folds covered-wins, so the covered module's data silently answers for the
    uncovered one. Measured: 100% where the truth was 0%.

    The module root is recovered by walking UP from the report and testing each ancestor
    against the conventional source roots, rather than by stripping known directory names.
    The layouts differ by build tool and by configuration:

        <module>/target/site/jacoco/jacoco.xml                 Maven, 3 levels up
        <module>/build/reports/jacoco/test/jacocoTestReport.xml  Gradle, 4 levels up

    and a name-stripping loop that knows `target`/`site`/`jacoco` stops dead on Gradle's
    `test` directory. Walking up asks the filesystem instead of guessing the shape.

    Anything that does not resolve is returned unchanged, so an unusual layout degrades
    to the old behaviour rather than inventing a path that matches nothing, which would
    drop the file from the denominator: the same bug by another route.
    """
    #: Maven and Gradle source roots. JaCoCo reports a path relative to one of these and
    #: never says which, so each is tested against the filesystem.
    roots = (
        "src/main/java",
        "src/main/kotlin",
        "src/main/scala",
        "src/main/groovy",
        "src/test/java",
        "src/test/kotlin",
    )
    # Enough to clear the deepest layout above with room to spare; bounded so an odd
    # report location cannot walk out to the filesystem root.
    ancestors = list(report_path.parents)[:6]
    cache: dict[str, str] = {}

    def resolve(rel: str) -> str:
        if rel in cache:
            return cache[rel]
        out = rel
        for base in ancestors:
            for root in roots:
                candidate = base / root / rel
                if candidate.is_file():
                    try:
                        out = candidate.relative_to(repo).as_posix()
                    except ValueError:
                        out = candidate.as_posix()
                    cache[rel] = out
                    return out
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
        if fmt is CoverageFormat.GOCOVER:
            # Go names files by import path and measures blocks, not lines: the module
            # path comes off via go.mod, and the source is what trims a block to the
            # lines its statements are on. See brimyr.coverage.gocover.
            root = Path(repo)
            return parse_gocover(
                text, resolve_path=_go_path_resolver(root), source_lines=_source_reader(root)
            )
        return parse_coverage_text(text, fmt)
    except (CoberturaError, JacocoError, GoCoverError) as exc:
        raise IngestError(str(exc)) from exc


#: `C:/src/x.cs` once normalized. A drive-letter path is absolute on the runner that
#: wrote it, and prefixing a project directory onto it would invent a path.
_WINDOWS_ABSOLUTE = re.compile(r"^[A-Za-z]:/")


def _project_root(eco: Ecosystem, repo: str | Path) -> Path:
    """Where ``eco``'s project lives: the repo itself, or the directory detection found."""
    return Path(repo) if eco.project_dir == "." else Path(repo) / eco.project_dir


def _onto_repo(report: CoverageReport, eco: Ecosystem, project_root: Path) -> CoverageReport:
    """Rewrite a nested project's report so its paths are repo-relative, like the diff's.

    A run in `backend/` names `app/main.py`, and the diff names `backend/app/main.py`.
    The suffix match would usually pair them anyway, which is exactly why this is not
    left to it: two projects that both have `src/index.ts` produce the SAME string,
    `merge_reports` folds them covered-wins, and the tested project answers for the
    untested one (the JaCoCo module-prefix bug again, by a new route).

    A path is prefixed only when the file really is under the project. Anything else
    (absolute, already repo-relative, rooted at some `<source>` the tool chose) is left
    exactly as written: inventing a path that names no file would drop it from the
    denominator, which is the silent pass this whole module exists to prevent.
    """
    if eco.project_dir == ".":
        return report

    def rename(path: str) -> str:
        if path.startswith("/") or _WINDOWS_ABSOLUTE.match(path):
            return path
        if not (project_root / path).is_file():
            return path
        return posixpath.normpath(f"{eco.project_dir}/{path}")

    return remap_paths(report, rename)


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
    #: What provisioning did, or why it did nothing. Diagnostic only — but it is the
    #: line that says whose environment the number was measured in.
    provision_note: str = ""
    #: The tests ran and passed, and this ecosystem produced no coverage BY NATURE —
    #: see :attr:`Ecosystem.coverage_optional`. A clean run with nothing measured, which
    #: is neither a broken run nor 0%: the third thing an empty report can mean, and the
    #: only one of the three that is both green and a real test result.
    unmeasured: bool = False

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

        An ``unmeasured`` outcome is the one exception, and only ecosystems that declare
        `coverage_optional` can produce one: a bats suite with no kcov on the runner
        emits no report however green it is, so demanding one would fail the repo for
        owning bash. It is NOT a licence to accept a missing report in general — the
        .NET test project whose `coverlet.collector` is absent is a genuine "can
        measure, did not" and stays red.
        """
        if self.unmeasured:
            return self.returncode == 0 and self.error is None
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
    def failed(self) -> tuple[Ecosystem, ...]:
        """The ecosystems whose run broke, so the summary can name them."""
        return tuple(o.ecosystem for o in self.outcomes if not o.ok)

    @property
    def coverage_paths(self) -> tuple[Path, ...]:
        """Every report across every ecosystem — what Sonar's reportPaths needs."""
        return tuple(path for o in self.outcomes for path in o.coverage_paths)

    @property
    def unmeasured(self) -> tuple[Ecosystem, ...]:
        """Ecosystems that ran clean and measured nothing, for the summary to name.

        A polyglot repo can be half-measured — `dotnet,shell` with no kcov reports real
        .NET coverage and no shell coverage at all — and the changed lines of the
        unmeasured half are then absent from the denominator rather than uncovered in
        it. Silence there is the vacuous-pass failure by a new route, so the caller has
        to be able to SAY which half is missing.
        """
        return tuple(o.ecosystem for o in self.outcomes if o.unmeasured)


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


#: A shell's exit status when the command it was told to run does not exist. The
#: difference between "your tests failed" and "nothing installed your test runner" is
#: the whole diagnosis, and it is the only thing separating them in the exit status.
_NOT_FOUND = 127


def run_one(
    eco: Ecosystem,
    repo: str | Path = ".",
    *,
    command: str | None = None,
    runner: Runner | None = None,
    timeout: float | None = DEFAULT_TEST_TIMEOUT,
    provision: bool = True,
    which: Which = shutil.which,
) -> RunOutcome:
    """Run a single ecosystem's tests and ingest its coverage file.

    Unless ``provision`` is off or ``command`` is given, the repo's own dependency
    manager is used to install what the run needs first — see :mod:`brimyr.provision`.
    """
    # Bound to the DEFAULT runner only: `Runner` is a two-argument contract and every
    # injected test runner implements it, so widening it here would break them all.
    run_fn = runner or functools.partial(_default_runner, timeout=timeout)
    # The project's own directory, not always the repo root: a nested project (see
    # detect._detect_nested) installs, runs and writes its report where it lives.
    root = _project_root(eco, repo)
    cwd = str(root)

    # An explicit `command` is the caller's own contract: they said how to run the
    # tests, so wrapping it in a dependency manager they did not ask for would change
    # what they asked to run. Their setup is theirs to do.
    prov = plan_provision(eco, root, which=which) if provision and command is None else Provision()
    cmd = command or prov.command or eco.command_str()

    for setup_cmd in prov.setup:
        try:
            setup = run_fn(setup_cmd, cwd)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return RunOutcome(
                eco,
                _NOT_FOUND,
                (),
                None,
                error=f"`{setup_cmd}` could not run: {exc}",
                provision_note=prov.note,
            )
        if setup.returncode != 0:
            # Not "0% coverage" and not a test failure: the dependencies never got
            # installed, so whatever the suite would have done next is meaningless.
            return RunOutcome(
                eco,
                setup.returncode,
                (),
                None,
                error=(
                    f"dependency install failed (`{setup_cmd}` exited "
                    f"{setup.returncode}). The tests were not run."
                ),
                provision_note=prov.note,
            )

    try:
        completed = run_fn(cmd, cwd)
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
            provision_note=prov.note,
        )
    except OSError as exc:
        return RunOutcome(
            eco,
            _NOT_FOUND,
            (),
            None,
            error=f"could not launch tests: {exc}",
            provision_note=prov.note,
        )

    coverage_files = locate_coverage_files(eco, root)
    if not coverage_files:
        # 127 means the shell never found the binary, so "did the test run emit
        # coverage?" is the wrong question and sent everyone looking at their coverage
        # config. Nothing ran. Say that, and say what would have made it run — the
        # note carries why provisioning declined, which is the actual fix.
        if completed.returncode == _NOT_FOUND:
            because = f" ({prov.note})" if prov.note else ""
            return RunOutcome(
                eco,
                _NOT_FOUND,
                (),
                None,
                error=(
                    f"the test command did not run: `{cmd}` — command not found{because}. "
                    "Nothing was measured. Install the test toolchain in the job, or set "
                    "`test_command` / `coverage_file`."
                ),
                provision_note=prov.note,
            )
        if eco.coverage_optional:
            # "No report" and "broken run" are the same observation with two causes, and
            # this ecosystem is the one where the harmless cause is the normal one: bats
            # emits nothing without kcov, and kcov is installed nowhere by default. A
            # green suite must not go red for that. The exit status still decides — a
            # failing suite is a failing suite, whatever it did or did not measure.
            if completed.returncode == 0:
                return RunOutcome(eco, 0, (), None, unmeasured=True, provision_note=prov.note)
            return RunOutcome(
                eco,
                completed.returncode,
                (),
                None,
                error=(
                    f"the tests failed (`{cmd}` exited {completed.returncode}). "
                    "This ecosystem measures no coverage, so the exit status is the "
                    "whole verdict."
                ),
                provision_note=prov.note,
            )
        return RunOutcome(
            eco,
            completed.returncode,
            (),
            None,
            error=(
                f"no coverage file found (expected one of: {', '.join(eco.coverage_paths)}). "
                "Did the test run emit coverage?"
            ),
            provision_note=prov.note,
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
            reports.append(ingest_file(path, eco.coverage_format, root))
        except IngestError as exc:
            # One unparseable report is a broken run, not a quietly smaller number.
            return RunOutcome(
                eco, completed.returncode, paths, None, error=str(exc), provision_note=prov.note
            )

    # Said per ecosystem, because a run can now span several projects and "the tests
    # failed" with no name attached sends the reader through every suite's log.
    failed = completed.returncode != 0
    return RunOutcome(
        eco,
        completed.returncode,
        paths,
        _onto_repo(merge_reports(reports), eco, root),
        error=f"the tests failed (`{cmd}` exited {completed.returncode})." if failed else None,
        provision_note=prov.note,
    )


def run_tests(
    ecosystems: list[Ecosystem],
    repo: str | Path = ".",
    *,
    command: str | None = None,
    runner: Runner | None = None,
    timeout: float | None = DEFAULT_TEST_TIMEOUT,
    provision: bool = True,
    which: Which = shutil.which,
) -> RunResult:
    """Run each ecosystem's tests and ingest coverage. ``command`` overrides all."""
    outcomes = [
        run_one(
            eco,
            repo,
            command=command,
            runner=runner,
            timeout=timeout,
            provision=provision,
            which=which,
        )
        for eco in ecosystems
    ]
    return RunResult(tuple(outcomes))
