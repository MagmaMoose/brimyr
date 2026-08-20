"""Render the coverage reports as a browsable HTML artifact — non-blocking.

Wraps `ReportGenerator <https://github.com/danielpalme/ReportGenerator>`_ (Apache-2.0)
rather than reimplementing an HTML renderer, and it is the *only* sane choice: it
auto-detects Cobertura, lcov and JaCoCo **per input file** — by XML root element, or a
``TN:``/``SF:`` prefix for lcov — so the whole list :mod:`brimyr.detect` produced can be
handed over without branching on format. Every alternative is single-format (``genhtml``
is lcov-only, ``coverage html`` needs the binary ``.coverage`` file, JaCoCo's CLI wants
``.exec`` plus compiled classes, not the XML), which would mean a converter per ecosystem
and three moving parts instead of one.

This exists because a repo with no SonarQube still deserves something to look at. It is
an **artifact**, deliberately, not a hosted page: a short-lived URL would mean storage,
auth, expiry and a leak surface (coverage percentages, repo names and the directory tree
of a private repo) in exchange for very little.

Same contract as :mod:`brimyr.sonar`: **failure-isolated**. Nothing here raises and the
gate never depends on it. No ReportGenerator on PATH, no .NET runtime, a malformed
report — all return ``ok=False`` and the run continues.

The one real cost is that ReportGenerator is framework-dependent .NET: there is no
self-contained build. GitHub-hosted runners ship the SDK on all three images, so
``dotnet tool install --tool-path`` needs no project and no setup step; container and
self-hosted runners need ``actions/setup-dotnet`` first.
"""

from __future__ import annotations

import subprocess  # nosec B404 - this module's job is to shell out to ReportGenerator
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# A runner takes (argv, cwd) and returns the completed process.
Runner = Callable[[list[str], str], subprocess.CompletedProcess]

DEFAULT_BIN = "reportgenerator"

# `HtmlInline` and not `Html`: it inlines the CSS and JS into each page, which is what
# makes the report readable after `actions/download-artifact` and a double-click. Plain
# `Html` references sibling asset files and renders unstyled from a `file://` URL, which
# is exactly how someone concludes the feature is broken.
#
# `MarkdownSummaryGithub` is deliberately NOT here. It is whole-project — the opposite of
# what this tool gates on — and the free build writes a `reportgenerator.io/pro` upsell
# row into the table. Brimyr renders its own summary.
DEFAULT_REPORT_TYPES = ("HtmlInline", "TextSummary")


@dataclass(frozen=True)
class HtmlReportResult:
    ok: bool
    message: str
    target_dir: Path | None = None
    returncode: int | None = None
    command: tuple[str, ...] = ()


def build_args(
    reports: Sequence[str | Path],
    target_dir: str | Path,
    *,
    report_types: Sequence[str] = DEFAULT_REPORT_TYPES,
    title: str = "",
    source_dirs: Sequence[str | Path] = (),
    class_filters: Sequence[str] = (),
    bin_path: str = DEFAULT_BIN,
) -> list[str]:
    """Assemble the ReportGenerator argv.

    ``-reports`` is semicolon-separated and every entry is sniffed independently, so a
    polyglot run can pass its Cobertura and its lcov in one list.
    """
    args = [
        bin_path,
        f"-reports:{';'.join(str(r) for r in reports)}",
        f"-targetdir:{target_dir}",
        f"-reporttypes:{';'.join(report_types)}",
    ]
    if title:
        args.append(f"-title:{title}")
    if source_dirs:
        # Without this the HTML shows percentages but no annotated source, because the
        # paths in a coverage report are relative to a root the renderer has to be told.
        args.append(f"-sourcedirs:{';'.join(str(d) for d in source_dirs)}")
    if class_filters:
        args.append(f"-classfilters:{';'.join(class_filters)}")
    return args


def _default_runner(argv: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(  # nosec B603 - argv is built here, never shell-interpolated
        argv, cwd=cwd, check=False, capture_output=True, text=True
    )


def render(
    reports: Sequence[str | Path],
    target_dir: str | Path,
    repo: str | Path = ".",
    *,
    report_types: Sequence[str] = DEFAULT_REPORT_TYPES,
    title: str = "",
    source_dirs: Sequence[str | Path] = (),
    class_filters: Sequence[str] = (),
    bin_path: str = DEFAULT_BIN,
    runner: Runner | None = None,
) -> HtmlReportResult:
    """Render ``reports`` to browsable HTML. Never raises."""
    if not reports:
        return HtmlReportResult(False, "skipped (no coverage reports to render)")

    argv = build_args(
        reports,
        target_dir,
        report_types=report_types,
        title=title,
        source_dirs=source_dirs,
        class_filters=class_filters,
        bin_path=bin_path,
    )
    run_fn = runner or _default_runner
    try:
        completed = run_fn(argv, str(repo))
    except FileNotFoundError:
        return HtmlReportResult(
            False,
            f"skipped ({bin_path} not found — "
            "`dotnet tool install --global dotnet-reportgenerator-globaltool`)",
            command=tuple(argv),
        )
    except OSError as exc:
        return HtmlReportResult(False, f"could not run {bin_path}: {exc}", command=tuple(argv))

    if completed.returncode == 0:
        return HtmlReportResult(
            True,
            "HTML coverage report generated",
            target_dir=Path(target_dir),
            returncode=0,
            command=tuple(argv),
        )
    detail = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "").strip()
    return HtmlReportResult(
        False,
        f"ReportGenerator failed (exit {completed.returncode}, non-blocking): {detail[:300]}",
        returncode=completed.returncode,
        command=tuple(argv),
    )
