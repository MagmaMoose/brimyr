"""Brimyr command-line interface.

Subcommands:

* ``brimyr coverage`` — the pure patch-coverage computation (coverage file(s) +
  base/head → patch coverage % + a gate exit code). Decoupled from GitHub Actions
  and unit-tested in isolation.
* ``brimyr ci`` — the full CI flow (detect ecosystem, run tests with coverage,
  compute patch coverage, gate, run sonar-scanner, ship).
* ``brimyr local`` — the same flow against a locally inferred base, to check a
  branch before pushing.
* ``brimyr version`` — print the version.

Exit codes: ``0`` pass · ``1`` patch coverage below threshold · ``2`` broken test
run / setup / usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from brimyr import __version__
from brimyr import git as bgit
from brimyr import report as report_mod
from brimyr import sonar as sonar_mod
from brimyr.coverage.diff import DiffIndex
from brimyr.coverage.model import CoverageReport, merge_reports
from brimyr.coverage.patch import PatchPolicy, compute_patch_coverage
from brimyr.detect import (
    CoverageFormat,
    Ecosystem,
    detect_ecosystems,
    ecosystem,
)
from brimyr.gate import (
    DEFAULT_THRESHOLD,
    EXIT_ERROR,
    GateDecision,
    decide_gate,
)
from brimyr.local import resolve_local_base
from brimyr.modes import Mode, resolve_mode
from brimyr.runner import IngestError, RunResult, ingest_file, run_tests

_EXT_FORMAT = {
    ".info": CoverageFormat.LCOV,
    ".lcov": CoverageFormat.LCOV,
    ".xml": CoverageFormat.COBERTURA,
}


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _fail(message: str) -> int:
    _eprint(f"brimyr: error: {message}")
    return EXIT_ERROR


def _parse_coverage_arg(spec: str) -> tuple[Path, CoverageFormat]:
    """Parse ``path[:format]`` (format ``lcov``|``cobertura``; inferred from ext)."""
    path_part, sep, fmt_part = spec.rpartition(":")
    # rpartition splits on the LAST ':'; on Windows a drive letter has a ':' too,
    # so only treat the tail as a format when it names one.
    if sep and fmt_part.strip().lower() in (f.value for f in CoverageFormat):
        return Path(path_part), CoverageFormat(fmt_part.strip().lower())
    path = Path(spec)
    fmt = _EXT_FORMAT.get(path.suffix.lower())
    if fmt is None:
        raise ValueError(
            f"cannot infer coverage format for {path} — append ':lcov' or ':cobertura'"
        )
    return path, fmt


def _patch_policy(args: argparse.Namespace, extra_prefixes: tuple[str, ...] = ()) -> PatchPolicy:
    return PatchPolicy(strip_prefixes=tuple(args.strip_prefix or ()) + extra_prefixes)


def counts_to_dict(decision: GateDecision) -> dict[str, object]:
    patch = decision.patch
    return {
        "patch_coverage": round(patch.percent, 2),
        "covered_lines": patch.covered_lines,
        "total_lines": patch.total_lines,
        "missing_lines": patch.missing_lines,
        "threshold": decision.threshold,
        # Mirror _emit_outputs: a broken run is an error, not a 0%/pass result.
        "gate_result": "error" if decision.broken else ("fail" if decision.failed else "pass"),
        "files": [
            {
                "path": f.path,
                "covered": f.covered,
                "total": f.total,
                "percent": round(f.percent, 2),
                "missing_lines": list(f.missing_lines),
            }
            for f in patch.files
        ],
    }


def _print_summary(decision: GateDecision, *, broken: bool) -> None:
    if broken:
        _eprint(
            "brimyr: BROKEN test run — tests failed or produced no coverage. "
            "This is a tool error (build red), not 0% patch coverage."
        )
        return
    patch = decision.patch
    _eprint(
        f"brimyr: patch coverage {patch.percent:.1f}% "
        f"({patch.covered_lines}/{patch.total_lines} changed executable lines covered)"
    )
    if not decision.gated:
        _eprint("brimyr: baseline run (report-only); not gating")
    elif not patch.has_measurable:
        _eprint("brimyr: no changed executable lines — vacuous pass")
    elif decision.failed:
        _eprint(f"brimyr: BELOW threshold {decision.threshold:.1f}% — uncovered changed lines:")
        for file_result in patch.files:
            if file_result.missing_lines:
                shown = ", ".join(str(n) for n in file_result.missing_lines[:15])
                more = " …" if len(file_result.missing_lines) > 15 else ""
                _eprint(f"  - {file_result.path}: {shown}{more}")
    else:
        _eprint(f"brimyr: meets threshold {decision.threshold:.1f}%")


def _emit_outputs(decision: GateDecision, *, mode: Mode | None, broken: bool) -> None:
    patch = decision.patch
    pairs = {
        "patch_coverage": f"{patch.percent:.2f}",
        "covered_lines": str(patch.covered_lines),
        "total_lines": str(patch.total_lines),
        "threshold": f"{decision.threshold:.2f}",
        "gate_result": "error" if broken else ("fail" if decision.failed else "pass"),
        "gate_failed": "true" if (broken or decision.failed) else "false",
    }
    if mode is not None:
        pairs["mode"] = mode.value
    report_mod.write_outputs(pairs)


# ── coverage: the pure patch-coverage computation ────────────────────────────


def cmd_coverage(args: argparse.Namespace) -> int:
    try:
        specs = [_parse_coverage_arg(s) for s in args.coverage_file]
    except ValueError as exc:
        return _fail(str(exc))

    reports: list[CoverageReport] = []
    for path, fmt in specs:
        try:
            reports.append(ingest_file(path, fmt))
        except IngestError as exc:
            return _fail(str(exc))
    report = merge_reports(reports)

    try:
        diff = bgit.compute_changed_lines(
            args.base, args.head, args.repo, use_merge_base=not args.no_merge_base
        )
    except bgit.GitError as exc:
        return _fail(str(exc))

    patch = compute_patch_coverage(diff, report, _patch_policy(args))
    try:
        decision = decide_gate(patch, args.threshold, gate=not args.no_gate)
    except ValueError as exc:
        return _fail(str(exc))

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(counts_to_dict(decision), indent=2), encoding="utf-8"
        )
    if not args.quiet:
        _print_summary(decision, broken=False)
    _emit_outputs(decision, mode=None, broken=False)
    return decision.exit_code


# ── ci / local: the full flow ────────────────────────────────────────────────


def _collect_coverage(
    args: argparse.Namespace,
) -> tuple[CoverageReport, bool, list[Ecosystem], dict[str, tuple[str, ...]]] | int:
    """Obtain coverage either from given files (escape hatch) or by running tests.

    Returns ``(report, broken, ecosystems, sonar_paths)`` or an error exit code.
    """
    # Escape hatch: ingest pre-made coverage file(s); never run tests.
    if args.coverage_file:
        try:
            specs = [_parse_coverage_arg(s) for s in args.coverage_file]
            reports = [ingest_file(path, fmt) for path, fmt in specs]
        except (ValueError, IngestError) as exc:
            return _fail(str(exc))
        return merge_reports(reports), False, [], {}

    # Otherwise detect (or honour forced) ecosystems and run their tests.
    if args.ecosystem:
        ecosystems: list[Ecosystem] = []
        for key in args.ecosystem:
            eco = ecosystem(key)
            if eco is None:
                return _fail(f"unknown ecosystem {key!r}")
            ecosystems.append(eco)
    else:
        ecosystems = detect_ecosystems(args.repo)

    if not ecosystems:
        return _fail(
            "no ecosystem detected — add a marker file, pass --ecosystem, or supply "
            "--coverage-file to ingest a pre-made report."
        )

    result: RunResult = run_tests(ecosystems, args.repo, command=args.test_command or None)
    sonar_paths: dict[str, tuple[str, ...]] = {}
    for outcome in result.outcomes:
        prop = outcome.ecosystem.sonar_property
        if prop and outcome.coverage_path is not None:
            sonar_paths[prop] = (*sonar_paths.get(prop, ()), str(outcome.coverage_path))
        if outcome.error:
            _eprint(f"brimyr: {outcome.ecosystem.label}: {outcome.error}")
    return result.report, result.broken, ecosystems, sonar_paths


def _maybe_run_sonar(
    args: argparse.Namespace, sonar_paths: dict[str, tuple[str, ...]]
) -> str | None:
    if not args.sonar_url:
        return None
    token = os.environ.get(args.sonar_token_env, "")
    config = sonar_mod.SonarConfig(
        host_url=args.sonar_url,
        token=token,
        project_key=args.sonar_project_key,
        organization=args.sonar_organization,
        sources=args.sonar_sources,
        coverage_report_paths=sonar_paths,
        extra_args=tuple(args.sonar_arg or ()),
    )
    result = sonar_mod.run_scanner(config, args.repo)
    return result.message


def _run_flow(args: argparse.Namespace, mode: Mode) -> int:
    collected = _collect_coverage(args)
    if isinstance(collected, int):
        return collected
    report, broken, ecosystems, sonar_paths = collected

    # Patch coverage only in gate mode; baseline computes nothing to gate on.
    if mode.gates and not broken:
        if not args.base:
            return _fail("PR/gate mode needs --base (the PR target ref).")
        try:
            diff = bgit.compute_changed_lines(
                args.base, args.head, args.repo, use_merge_base=not args.no_merge_base
            )
        except bgit.GitError as exc:
            return _fail(str(exc))
        repo_abs = str(Path(args.repo).resolve())
        patch = compute_patch_coverage(diff, report, _patch_policy(args, (repo_abs,)))
    else:
        patch = compute_patch_coverage(DiffIndex(()), report)

    try:
        decision = decide_gate(patch, args.threshold, broken=broken, gate=mode.gates)
    except ValueError as exc:
        return _fail(str(exc))

    sonar_message = _maybe_run_sonar(args, sonar_paths) if not broken else None

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(counts_to_dict(decision), indent=2), encoding="utf-8"
        )

    summary = report_mod.render_summary(
        decision, mode, broken=broken, ecosystems=ecosystems, sonar_message=sonar_message
    )
    report_mod.append_step_summary(summary)
    if not args.quiet:
        _print_summary(decision, broken=broken)
        if sonar_message:
            _eprint(f"brimyr: SonarQube: {sonar_message}")
    _emit_outputs(decision, mode=mode, broken=broken)
    return decision.exit_code


def cmd_ci(args: argparse.Namespace) -> int:
    mode = resolve_mode(args.mode, os.environ.get("GITHUB_EVENT_NAME"))
    return _run_flow(args, mode)


def cmd_local(args: argparse.Namespace) -> int:
    base = resolve_local_base(args.repo, args.base)
    if base is None:
        return _fail("could not infer a base branch to diff against — pass --base explicitly.")
    args.base = base
    if not args.quiet:
        _eprint(f"brimyr: local run against base {base!r}")
    return _run_flow(args, Mode.PR)


def cmd_version(_args: argparse.Namespace) -> int:
    print(__version__)
    return 0


# ── argument parser ──────────────────────────────────────────────────────────


def _add_shared_diff_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--head", default="HEAD", help="Head ref/SHA (default: HEAD).")
    parser.add_argument("--repo", default=".", help="Path to the git repository (default: .).")
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=f"Patch-coverage threshold that blocks below it (default: {DEFAULT_THRESHOLD}).",
    )
    parser.add_argument(
        "--strip-prefix",
        action="append",
        metavar="PREFIX",
        help="Path prefix to strip from coverage paths before matching (repeatable).",
    )
    parser.add_argument(
        "--no-merge-base",
        action="store_true",
        help="Diff base..head directly instead of merge-base(base, head)..head.",
    )
    parser.add_argument("--json-out", help="Write the patch-coverage summary as JSON here.")
    parser.add_argument("--quiet", action="store_true", help="Suppress the human summary.")


def _add_sonar_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--sonar-url", help="SonarQube host URL (enables a non-blocking sonar-scanner run)."
    )
    parser.add_argument(
        "--sonar-token-env", default="SONAR_TOKEN", help="Env var holding the Sonar token."
    )
    parser.add_argument("--sonar-project-key", help="Sonar project key.")
    parser.add_argument("--sonar-organization", help="Sonar organization (SonarCloud).")
    parser.add_argument("--sonar-sources", default=".", help="sonar.sources value (default: .).")
    parser.add_argument(
        "--sonar-arg",
        action="append",
        metavar="ARG",
        help="Extra raw sonar-scanner arg, e.g. -Dsonar.foo=bar (repeatable).",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brimyr",
        description="Patch-coverage gate: run tests with coverage, gate on changed-line coverage.",
    )
    parser.add_argument("--version", action="version", version=f"brimyr {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    cov = sub.add_parser(
        "coverage",
        help="Compute patch coverage from coverage file(s) + base/head and gate on it.",
        description=(
            "Given coverage file(s) and a base/head, compute the coverage of the lines "
            "the diff introduced and decide pass/fail. Pure — runs no tests."
        ),
    )
    cov.add_argument(
        "--coverage-file",
        action="append",
        required=True,
        metavar="PATH[:FORMAT]",
        help="Coverage file (format lcov|cobertura; inferred from extension). Repeatable.",
    )
    cov.add_argument("--base", required=True, help="Base ref/SHA (PR target).")
    _add_shared_diff_args(cov)
    cov.add_argument("--no-gate", action="store_true", help="Always exit 0 (report only).")
    cov.set_defaults(func=cmd_coverage)

    ci = sub.add_parser(
        "ci",
        help="Full CI flow: detect ecosystem, run tests w/ coverage, gate, ship to Sonar.",
        description=(
            "Detect the repo's ecosystem(s), run the right test command with coverage, "
            "gate on patch coverage (PR events), and run sonar-scanner (non-blocking)."
        ),
    )
    ci.add_argument(
        "--mode",
        choices=["auto", *[m.value for m in Mode]],
        default="auto",
        help="auto (from GITHUB_EVENT_NAME), pr (patch gate), or baseline (no gate).",
    )
    ci.add_argument("--base", help="Base ref/SHA (required in PR/gate mode).")
    _add_shared_diff_args(ci)
    ci.add_argument(
        "--coverage-file",
        action="append",
        metavar="PATH[:FORMAT]",
        help="Escape hatch: ingest a pre-made coverage file instead of running tests. Repeatable.",
    )
    ci.add_argument(
        "--ecosystem",
        action="append",
        metavar="KEY",
        help="Force an ecosystem (python|javascript|dotnet) instead of auto-detect. Repeatable.",
    )
    ci.add_argument(
        "--test-command",
        help="Override the detected test command (a shell command string).",
    )
    _add_sonar_args(ci)
    ci.set_defaults(func=cmd_ci)

    local = sub.add_parser(
        "local",
        help="Run the patch-coverage gate against a locally inferred base (pre-push check).",
    )
    local.add_argument(
        "--base", help="Base ref to diff against (default: the repo's default branch)."
    )
    _add_shared_diff_args(local)
    local.add_argument(
        "--coverage-file",
        action="append",
        metavar="PATH[:FORMAT]",
        help="Ingest a pre-made coverage file instead of running tests. Repeatable.",
    )
    local.add_argument(
        "--ecosystem", action="append", metavar="KEY", help="Force an ecosystem. Repeatable."
    )
    local.add_argument("--test-command", help="Override the detected test command.")
    _add_sonar_args(local)
    local.set_defaults(func=cmd_local)

    version = sub.add_parser("version", help="Print the brimyr version.")
    version.set_defaults(func=cmd_version)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
