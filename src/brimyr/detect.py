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
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path


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
    # Optional extra confirmation beyond bare marker presence. When set, the
    # ecosystem is only auto-detected if this also returns True for the repo root —
    # a guard against markers that don't imply a real test run (e.g. a package.json
    # shipped only for frontend assets). Bypassed by an explicit ``--ecosystem``.
    confirm: Callable[[Path], bool] | None = None

    def command_str(self) -> str:
        return " ".join(self.test_command)


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
        markers=("*.sln", "*.csproj", "*.fsproj", "*.vbproj"),
        test_command=(
            "dotnet",
            "test",
            '--collect:"XPlat Code Coverage"',
            "--results-directory",
            "./TestResults",
        ),
        coverage_format=CoverageFormat.COBERTURA,
        coverage_paths=("TestResults/**/coverage.cobertura.xml",),
        # The patch-coverage gate works from this Cobertura file directly. Shipping
        # .NET coverage to Sonar needs the dedicated SonarScanner for .NET
        # (begin/end), not a plain `sonar-scanner` -D property, so leave it unset.
        sonar_property="",
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
        confirm=_java_is_maven,
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
    """Look up a built-in ecosystem by key (python | javascript | dotnet | java)."""
    return _BY_KEY.get(key.strip().lower())


def _has_marker(root: Path, markers: tuple[str, ...]) -> bool:
    for marker in markers:
        if "*" in marker or "?" in marker:
            if any(root.glob(marker)):
                return True
        elif (root / marker).exists():
            return True
    return False


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
    return [_VITEST if eco.key == "javascript" and _js_uses_vitest(root) else eco for eco in found]


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
