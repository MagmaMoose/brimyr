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

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path


class CoverageFormat(StrEnum):
    LCOV = "lcov"
    COBERTURA = "cobertura"


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

    def command_str(self) -> str:
        return " ".join(self.test_command)


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
)

_BY_KEY = {eco.key: eco for eco in ECOSYSTEMS}


def ecosystem(key: str) -> Ecosystem | None:
    """Look up a built-in ecosystem by key (python | javascript | dotnet)."""
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
    """Every built-in ecosystem whose marker files are present in ``repo``."""
    root = Path(repo)
    return [eco for eco in ECOSYSTEMS if _has_marker(root, eco.markers)]


def locate_coverage_file(eco: Ecosystem, repo: str | Path = ".") -> Path | None:
    """Find the coverage file an ecosystem's run should have produced, or None."""
    root = Path(repo)
    for pattern in eco.coverage_paths:
        if "*" in pattern or "?" in pattern:
            matches = sorted(root.glob(pattern), key=lambda p: p.stat().st_mtime, reverse=True)
            if matches:
                return matches[0]
        else:
            candidate = root / pattern
            if candidate.is_file():
                return candidate
    return None
