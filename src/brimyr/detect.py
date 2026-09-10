"""Auto-detect the repo's ecosystem(s) and the right test-with-coverage command.

Like Diatreme, Brimyr sniffs marker files in the repo root and maps each match to
a built-in :class:`Ecosystem`: a default test command that turns coverage
instrumentation **on**, the coverage file format it emits, and where that file
lands. Coverage is a *byproduct of the test run*, not a step after it — the
command both runs the tests and writes the coverage file in one pass.

Polyglot repos (a JS frontend + a Python backend) match more than one ecosystem
and produce **one coverage file per language**; the CLI runs each and merges the
reports. Anything here can be overridden from the action (``test_command`` /
``coverage_file``) — detection is the convenient default, never a cage.
"""

from __future__ import annotations

import json
import shlex
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path


class SonarStrategy(StrEnum):
    """How an ecosystem's analysis reaches SonarQube.

    Not a detail: it changes *when* the scanner runs. ``CLI`` is a post-step — the
    coverage files already exist and their paths are passed as ``-D`` properties.
    ``DOTNET`` is not, and cannot be made one: SonarSource documents that the
    SonarScanner CLI "doesn't support C# or VB.NET analysis" at all, because C#
    issues come from Roslyn analyzers that ``dotnet sonarscanner begin`` injects
    into the compilation. No compile between ``begin`` and ``end`` means no
    analysis, so the scanner has to *wrap* the build and test run rather than
    follow it.
    """

    CLI = "cli"
    DOTNET = "dotnet"


class CoverageFormat(StrEnum):
    LCOV = "lcov"
    COBERTURA = "cobertura"
    JACOCO = "jacoco"


@dataclass(frozen=True)
class Ecosystem:
    """A detectable language toolchain and how it emits coverage."""

    key: str
    label: str
    markers: tuple[str, ...]
    test_command: tuple[str, ...]
    coverage_format: CoverageFormat
    # Candidate output paths (repo-relative); may contain glob patterns. The first
    # existing match locates the coverage file after the run.
    coverage_paths: tuple[str, ...]
    # SonarQube property a sonar-scanner run uses to ingest this report.
    sonar_property: str = ""
    # Which scanner, and therefore whether it wraps the run or follows it.
    sonar_strategy: SonarStrategy = SonarStrategy.CLI
    # DOTNET only. Wildcards handed to `begin`, BEFORE any report file exists —
    # `end` accepts only three flags, so the coverage path must be declared up front.
    sonar_report_globs: tuple[str, ...] = ()
    # DOTNET only. The compile that has to sit inside the begin/end window.
    # `--no-incremental` is not optional: a cached build compiles nothing, so the
    # analyzers `begin` injected never run and `end` finds no analysis data.
    sonar_build_command: tuple[str, ...] = ()
    # Properties the caller MUST supply (via sonar_args) or the analysis is skipped
    # with a warning instead of being run and producing junk.
    sonar_required_props: tuple[str, ...] = ()
    # Optional extra confirmation beyond bare marker presence. When set, the
    # ecosystem is only auto-detected if this also returns True for the repo root —
    # a guard against markers that don't imply a real test run (e.g. a package.json
    # shipped only for frontend assets). Bypassed by an explicit ``--ecosystem``.
    confirm: Callable[[Path], bool] | None = None
    # True when a PASSING test run legitimately produces no coverage file at all, so
    # its absence is a measurement gap rather than a broken run. Off for every
    # ecosystem whose runner instruments as it goes (pytest, jest, coverlet, JaCoCo):
    # there, no report means the run broke, and saying otherwise would turn a real
    # failure — a .NET test project with no `coverlet.collector` — into a green gate.
    # On for `shell`, where coverage needs kcov, kcov is installed nowhere by default,
    # and a bats suite running without it is the NORMAL outcome, not a broken one.
    coverage_optional: bool = False
    # Why this ecosystem may measure nothing, said in the summary next to the gap.
    # An unmeasured half has to name itself, or a repo whose shell scripts are simply
    # not in the denominator reads as a repo whose shell scripts are covered.
    coverage_note: str = ""

    def command_str(self) -> str:
        return " ".join(self.test_command)


#: Directory names a test-file search must never descend into. On a fresh checkout none
#: of these exist yet, but `brimyr local` runs against a working tree where they all do,
#: and one vendored `test_foo.py` under .venv would make every repo look tested.
_VENDOR_DIRS = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".tox",
        ".nox",
        ".venv",
        "venv",
        "env",
        "node_modules",
        "site-packages",
        "vendor",
        "third_party",
        ".mypy_cache",
        ".pytest_cache",
    }
)

#: Where a pytest configuration lives, and the section that makes it one. `tox.ini` and
#: `setup.cfg` are also bare python MARKERS, so their presence alone proves nothing; the
#: section inside is the signal.
# A directory carrying one of these is a Python project in its own right. Used to tell
# "this repo's tests" from "a nested deployable's tests" — see _owned_by_root.
_PYTHON_PROJECT_MARKERS: tuple[str, ...] = ("pyproject.toml", "setup.py", "setup.cfg")

_PYTEST_CONFIG_SECTIONS: tuple[tuple[str, str], ...] = (
    ("pyproject.toml", "[tool.pytest.ini_options]"),
    ("pytest.ini", "[pytest]"),
    ("setup.cfg", "[tool:pytest]"),
    ("tox.ini", "[pytest]"),
)


def _python_has_test_signal(root: Path) -> bool:
    """True if the repo has a real Python test setup, not just a packaging marker.

    The python markers are the most over-broad of any ecosystem here: a
    `requirements.txt` or a `pyproject.toml` is shipped by repos with no Python source
    at all (a `pre-commit` config's pins, a docs build, a Terraform repo's tooling).
    Detecting python off the bare marker runs `pytest --cov`, which exits 5 with "no
    tests ran" — an empty coverage report, which the broken-run rule then correctly
    reads as a tool error and turns the build red. A repo with nothing to test is not a
    broken repo, so it must not be detected in the first place.

    Exactly the reasoning behind :func:`_js_has_test_signal` and :func:`_java_is_maven`;
    python was simply the one marker set that never got the guard. Bypassed by an
    explicit ``--ecosystem python``, which is the escape hatch for a layout this misses.

    The file fallback only counts tests belonging to the ROOT project. A nested
    deployable with its own ``pyproject.toml`` (``broker/tests/``) is a different
    project whose dependencies are not in this environment, so `pytest` at the root
    collects those files and dies importing them. Diatreme is the live case: bash and
    TypeScript, a root ``pyproject.toml`` holding nothing but ``[tool.semantic_release]``,
    and its only Python under ``broker/``. Recursion itself is kept — a ``src/`` layout
    with ``tests/`` at the root, or tests beside the code, must still be found.

    An explicit root pytest config still wins outright and is checked first: a repo that
    configures ``testpaths`` has *said* pytest runs from the root, whatever the layout.
    """
    for name, section in _PYTEST_CONFIG_SECTIONS:
        try:
            if section in (root / name).read_text(encoding="utf-8", errors="ignore"):
                return True
        except OSError:
            continue
    return _has_test_file(
        root, ("test_*.py", "*_test.py"), nested_project_markers=_PYTHON_PROJECT_MARKERS
    )


def _owned_by_root(root: Path, match: Path, markers: tuple[str, ...]) -> bool:
    """True if no NESTED project of its own sits between ``root`` and ``match``.

    A test file under a directory that declares its own ``pyproject.toml`` belongs to
    that project, not this one. Its dependencies live in that project's environment,
    which the root's does not have, so `pytest` at the root collects it and then dies
    importing it — the empty report the broken-run rule turns red.

    Note the root's OWN marker is skipped, not treated as nested: every Python repo
    has one, and it is what makes these the root project's tests in the first place.
    """
    for parent in match.relative_to(root).parents:
        if parent == Path("."):
            continue
        if any((root / parent / marker).is_file() for marker in markers):
            return False
    return True


def _has_test_file(
    root: Path,
    patterns: tuple[str, ...],
    *,
    nested_project_markers: tuple[str, ...] = (),
) -> bool:
    """True as soon as ONE non-vendored file matches, without walking the rest.

    Short-circuits, so the common case (a repo that has tests) is cheap; only a repo
    with none pays for the full walk, and that is the answer we need to be sure of.

    ``nested_project_markers`` additionally requires a match to belong to the ROOT
    project — see :func:`_owned_by_root`. Off by default so this stays a plain
    "is there a test file" question for any other caller.
    """
    for pattern in patterns:
        for match in root.glob(f"**/{pattern}"):
            if not _VENDOR_DIRS.isdisjoint(match.relative_to(root).parts):
                continue
            if nested_project_markers and not _owned_by_root(root, match, nested_project_markers):
                continue
            return True
    return False


def _js_has_test_signal(root: Path) -> bool:
    """True if the repo has a real JS/TS test setup, not just a bare package.json.

    A ``package.json`` is shipped by many repos that have *no* JS tests at all — a
    Python/Go/etc. backend bundling a frontend or build tooling. Detecting JS off
    the bare marker would run ``jest``, find no ``coverage/lcov.info``, and trip the
    broken-run rule into a red build. Require an actual signal: a jest/vitest config
    file, or a ``package.json`` that declares a non-placeholder ``test`` script.
    """
    if any(
        any(root.glob(f"{tool}.config.{ext}"))
        for tool in ("jest", "vitest")
        for ext in ("js", "cjs", "mjs", "ts", "json")
    ):
        return True
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    scripts = data.get("scripts") if isinstance(data, dict) else None
    test_script = scripts.get("test") if isinstance(scripts, dict) else None
    if not isinstance(test_script, str) or not test_script.strip():
        return False
    # `npm init` writes a placeholder `test` script that just errors out; not a run.
    return "no test specified" not in test_script


def _js_uses_vitest(root: Path) -> bool:
    """True if the repo's JS tests run under vitest rather than jest.

    Vue/Vite projects are overwhelmingly vitest, and `_js_has_test_signal` already
    counts a `vitest.config.*` as a real test signal — so without this the repo is
    detected as JS and then handed the *jest* command, which fails the run and turns
    the build red. Same coverage output (`coverage/lcov.info`), different binary.
    """
    if any(
        any(root.glob(f"vitest.config.{ext}")) for ext in ("js", "cjs", "mjs", "ts", "mts", "json")
    ):
        return True
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    deps = {**data.get("devDependencies", {}), **data.get("dependencies", {})}
    if "vitest" in deps:
        return True
    # Also detect 'vitest' in the test script string.
    scripts = data.get("scripts")
    test_script = scripts.get("test") if isinstance(scripts, dict) else None
    return isinstance(test_script, str) and "vitest" in test_script


def _java_is_maven(root: Path) -> bool:
    """True only for a Maven build — the built-in test command is `mvn`.

    `build.gradle` is a marker because a Gradle repo IS a Java repo worth naming in
    a detection failure, but running `mvn` in one would fail the run and trip the
    broken-run rule into a red build. Gradle users pass `test_command` +
    `coverage_file` explicitly (the JaCoCo *parser* is shared — only the invocation
    differs), so refusing to auto-detect here is the honest default rather than a
    guess that breaks their pipeline.
    """
    return (root / "pom.xml").is_file()


#: Directories a bats search must never descend into, on top of :data:`_VENDOR_DIRS`.
#: The conventional bats layout VENDORS ITS OWN FRAMEWORK — `test/bats` is bats-core as a
#: submodule, `test/test_helper/bats-support` and friends are its helper libraries — and
#: bats-core's checkout carries its own `test/*.bats` suite. Without this, detection finds
#: those, hands them to `bats`, and the gate runs the test suite of the framework instead
#: of the repo's. A repo that genuinely keeps its own tests in a directory called `bats`
#: pays for that with `test_command`.
_BATS_VENDOR_DIRS = frozenset(
    {
        "bats",
        "bats-core",
        "test_helper",
        "bats-support",
        "bats-assert",
        "bats-file",
        "bats-mock",
        "bats-detik",
    }
)

#: The invocation; the targets are discovered per repo and appended. `--recursive` is
#: what makes a directory argument mean "every .bats under here".
_BATS_COMMAND: tuple[str, ...] = ("bats", "--recursive")


def _shell_test_targets(root: Path) -> tuple[str, ...]:
    """The directories (and root-level files) to hand `bats`, shallowest-first.

    A `*.bats` file IS the confirming predicate, not merely a marker: the marker set has
    to include `test`/`tests`/`*/tests` to find a suite that is not at the root, and
    those directory names are shipped by practically every repo on earth. `pyproject.toml`
    is the cautionary tale — a marker with no confirming predicate detected Python in
    every repo that had a docs build, ran `pytest --cov`, and turned it red. Here the
    marker only decides whether it is worth looking; this decides.

    Immediate parents rather than a single root: passing `.` would make `--recursive`
    walk `node_modules` and every vendored checkout. A directory that is a descendant of
    another target is dropped, because `--recursive` already covers it, and a root-level
    `.bats` file is passed as itself for the same reason `.` is not used.
    """
    dirs: set[str] = set()
    files: set[str] = set()
    for match in sorted(root.glob("**/*.bats")):
        parts = match.relative_to(root).parts
        if not _VENDOR_DIRS.isdisjoint(parts) or not _BATS_VENDOR_DIRS.isdisjoint(parts[:-1]):
            continue
        parent = match.parent.relative_to(root).as_posix()
        if parent == ".":
            files.add(match.name)
        else:
            dirs.add(parent)
    # `--recursive` already descends, so a nested target would run the same file twice.
    pruned = sorted(
        d for d in dirs if not any(other != d and d.startswith(f"{other}/") for other in dirs)
    )
    return (*pruned, *sorted(files))


def _shell_has_bats(root: Path) -> bool:
    """True only if a real, non-vendored `.bats` file exists — see :func:`_shell_test_targets`."""
    return bool(_shell_test_targets(root))


# Built-in ecosystems. Order is the detection/run order for polyglot repos.
ECOSYSTEMS: tuple[Ecosystem, ...] = (
    Ecosystem(
        key="python",
        label="Python",
        markers=("pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "tox.ini"),
        test_command=(
            "pytest",
            "--cov",
            "--cov-report=xml",
            "--cov-report=term-missing",
        ),
        coverage_format=CoverageFormat.COBERTURA,
        coverage_paths=("coverage.xml",),
        sonar_property="sonar.python.coverage.reportPaths",
        confirm=_python_has_test_signal,
    ),
    Ecosystem(
        key="javascript",
        label="JavaScript / TypeScript",
        markers=("package.json",),
        test_command=(
            "npx",
            "--yes",
            "jest",
            "--coverage",
            "--coverageReporters=lcov",
            "--coverageReporters=text-summary",
            "--passWithNoTests",
        ),
        coverage_format=CoverageFormat.LCOV,
        coverage_paths=("coverage/lcov.info",),
        sonar_property="sonar.javascript.lcov.reportPaths",
        confirm=_js_has_test_signal,
    ),
    Ecosystem(
        key="dotnet",
        label=".NET",
        # `.slnx` is the XML solution format that became the default in .NET 10, so
        # `dotnet new sln` now writes `Foo.slnx` and a repo created with current tooling
        # has NO `.sln` at all. Projects usually live under src/, and _has_marker only
        # globs the root, so the solution file is often the only marker there is: missing
        # `.slnx` means a whole modern solution is silently not detected.
        markers=("*.sln", "*.slnx", "*.csproj", "*.fsproj", "*.vbproj"),
        test_command=(
            "dotnet",
            "test",
            '--collect:"XPlat Code Coverage"',
            "--results-directory",
            "./TestResults",
        ),
        coverage_format=CoverageFormat.COBERTURA,
        coverage_paths=("TestResults/**/coverage.cobertura.xml",),
        # NOTE the plural: `reportsPaths`, unlike sonar.python.coverage.reportPaths
        # and sonar.javascript.lcov.reportPaths. Sonar is not consistent here and the
        # singular form is silently ignored.
        sonar_property="sonar.cs.cobertura.reportsPaths",
        sonar_strategy=SonarStrategy.DOTNET,
        # Declared at `begin`, so it has to be a wildcard — the reports do not exist
        # yet, and a solution writes one per test project. Sonar documents this
        # property as comma-delimited WITH wildcard support, which is what makes the
        # multi-test-project case work without enumerating GUID directories.
        sonar_report_globs=("**/TestResults/**/coverage.cobertura.xml",),
        sonar_build_command=("dotnet", "build", "--no-incremental", "--disable-build-servers"),
    ),
    Ecosystem(
        key="java",
        label="Java / JVM",
        markers=("pom.xml", "build.gradle", "build.gradle.kts"),
        test_command=(
            "mvn",
            "-B",
            "org.jacoco:jacoco-maven-plugin:prepare-agent",
            "test",
            "org.jacoco:jacoco-maven-plugin:report",
        ),
        coverage_format=CoverageFormat.JACOCO,
        # Multi-module reactors are the norm on the JVM, and each module writes its
        # OWN report under its own target/. The leading `**/` is what picks up every
        # module; a single-module build matches the same pattern at depth 0.
        coverage_paths=(
            "**/target/site/jacoco/jacoco.xml",
            "**/build/reports/jacoco/**/*.xml",
        ),
        sonar_property="sonar.coverage.jacoco.xmlReportPaths",
        # `sonar-scanner -Dsonar.sources=.` over a Java repo fails outright with
        # "please provide compiled classes with sonar.java.binaries", and Sonar
        # separately documents that the CLI scanner should not be used on Maven
        # projects at all. Rather than ship a run that cannot succeed, require the
        # caller to name the binaries via `sonar_args` — otherwise the analysis is
        # skipped with a warning that says exactly that.
        sonar_required_props=("sonar.java.binaries",),
        confirm=_java_is_maven,
    ),
    Ecosystem(
        key="shell",
        label="Shell",
        # Deliberately over-broad, and safe only because `confirm` is strict: a bats
        # suite lives under `test/`, `tests/` or `scripts/tests/` far more often than at
        # the repo root, and `_has_marker` globs the root only. A `tests` directory means
        # "look for a .bats file", never "this is a shell repo".
        markers=("*.bats", "test", "tests", "*/tests"),
        # Placeholder: `for_repo` replaces the target list with the directories that
        # actually hold .bats files. Kept as a sane default so a forced `--ecosystem
        # shell` on a layout the search misses still runs something nameable.
        test_command=(*_BATS_COMMAND, "tests"),
        # kcov is the only realistic bats coverage tool and it writes Cobertura, one
        # per traced binary plus a merged one. Brimyr never installs it (see
        # `provision._shell_plan`), so these paths exist only when the runner already
        # has kcov — which is why this ecosystem is `coverage_optional`.
        coverage_format=CoverageFormat.COBERTURA,
        coverage_paths=("coverage/kcov/**/cobertura.xml",),
        # No `sonar_property`: SonarQube has no importer for shell coverage, and
        # guessing one would upload the report under a property Sonar silently ignores.
        coverage_optional=True,
        coverage_note=(
            "shell coverage needs kcov, which brimyr does not install; install it in the "
            "job to measure bash"
        ),
        confirm=_shell_has_bats,
    ),
)

_BY_KEY_SOURCE = {eco.key: eco for eco in ECOSYSTEMS}

# The jest table entry with only the command swapped: same markers, same lcov output at
# `coverage/lcov.info`, same Sonar property. Not a separate ECOSYSTEMS row, because it
# would double-match every `package.json` in a polyglot repo and run the suite twice.
_VITEST = replace(
    _BY_KEY_SOURCE["javascript"],
    label="JavaScript / TypeScript (vitest)",
    test_command=(
        "npx",
        "--yes",
        "vitest",
        "run",
        "--coverage",
        "--coverage.reporter=lcov",
        "--coverage.reporter=text-summary",
        "--passWithNoTests",
    ),
)

_BY_KEY = {**_BY_KEY_SOURCE, "vitest": _VITEST}


def ecosystem(key: str) -> Ecosystem | None:
    """Look up a built-in ecosystem by key (python | javascript | dotnet | java | shell).

    The table's own row, untailored. Callers holding a repo path should pass the result
    through :func:`for_repo`.
    """
    return _BY_KEY.get(key.strip().lower())


def _has_marker(root: Path, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if "*" in marker or "?" in marker:
            if any(root.glob(marker)):
                return True
        elif (root / marker).exists():
            return True
    return False


def for_repo(eco: Ecosystem, repo: str | Path = ".") -> Ecosystem:
    """Fill in the part of a row that only the checkout can supply.

    The table says what an ecosystem IS; `shell` is the one row whose *arguments* are
    unknowable from it, because `bats` has to be pointed at the directories that hold
    the repo's `.bats` files.

    Applied to a FORCED ``--ecosystem`` as well as to detection, which is the whole
    reason it is a function rather than a line inside :func:`detect_ecosystems`. Forcing
    is what a consumer does when detection does not fire, and handing them the table's
    placeholder would run `bats --recursive tests` in a repo whose suite is elsewhere.

    The jest/vitest swap deliberately does NOT live here. It picks a different *binary*,
    ``--ecosystem vitest`` already exists for asking, and a consumer who forces
    ``javascript`` today gets jest — moving the swap onto the forced path under a pinned
    tag could turn a working repo's run into a failing one.
    """
    if eco.key == "shell":
        targets = _shell_test_targets(Path(repo))
        if targets:
            # Quoted here and not in the search, which returns paths as data. The command
            # is handed to a shell, and a discovered path is the one part of it that
            # nobody wrote by hand: a directory with a space in it would otherwise arrive
            # as two arguments and the run would fail on a name. `shlex.quote` leaves an
            # ordinary path exactly as it was.
            return replace(eco, test_command=(*_BATS_COMMAND, *map(shlex.quote, targets)))
    return eco


def detect_ecosystems(repo: str | Path = ".") -> list[Ecosystem]:
    """Every built-in ecosystem whose markers are present in ``repo``.

    An ecosystem with a ``confirm`` predicate must also pass it — markers alone can
    over-detect (a bare ``package.json`` with no JS tests). Force one explicitly
    with ``--ecosystem`` to bypass detection entirely.
    """
    root = Path(repo)
    found = [
        eco
        for eco in ECOSYSTEMS
        if _has_marker(root, eco.markers) and (eco.confirm is None or eco.confirm(root))
    ]
    return [
        for_repo(_VITEST if eco.key == "javascript" and _js_uses_vitest(root) else eco, root)
        for eco in found
    ]


def locate_coverage_files(eco: Ecosystem, repo: str | Path = ".") -> list[Path]:
    """Every coverage file an ecosystem's run produced, in a deterministic order.

    ALL of them, not the newest. `dotnet test` on a solution writes one
    ``TestResults/<guid>/coverage.cobertura.xml`` PER TEST PROJECT, so a solution with
    five test projects leaves five reports. Taking only the most recent one silently
    drops the other four — and because :mod:`brimyr.coverage.patch` treats a file the
    report never mentions as contributing nothing, every changed file belonging to a
    dropped project vanishes from the denominator instead of failing loudly. The gate
    then reports a comfortable, meaningless pass.

    Sorted by path rather than mtime so two runs over the same tree merge identically;
    ``merge_reports`` is covered-wins and order-independent, but a stable order keeps
    the Sonar `reportPaths` list and any diagnostics reproducible.
    """
    root = Path(repo)
    found: list[Path] = []
    for pattern in eco.coverage_paths:
        if "*" in pattern or "?" in pattern:
            found.extend(sorted(p for p in root.glob(pattern) if p.is_file()))
        else:
            candidate = root / pattern
            if candidate.is_file():
                found.append(candidate)
    # A pattern can overlap a literal path; keep first occurrence only.
    seen: set[Path] = set()
    return [p for p in found if not (p in seen or seen.add(p))]


def locate_coverage_file(eco: Ecosystem, repo: str | Path = ".") -> Path | None:
    """The first coverage file, or None. Prefer :func:`locate_coverage_files`.

    Kept because a single path is genuinely the right answer for the single-report
    ecosystems (pytest writes one coverage.xml, jest one lcov.info) and it keeps the
    existing callers and tests meaningful.
    """
    files = locate_coverage_files(eco, repo)
    return files[0] if files else None
