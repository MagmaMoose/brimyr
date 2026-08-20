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
from collections.abc import Sequence
from pathlib import Path

from brimyr import __version__, broker_client, sonar_dotnet
from brimyr import git as bgit
from brimyr import github_comment as comment_mod
from brimyr import report as report_mod
from brimyr import sonar as sonar_mod
from brimyr.coverage.diff import DiffIndex
from brimyr.coverage.jacoco import is_jacoco
from brimyr.coverage.model import CoverageReport, merge_reports
from brimyr.coverage.patch import PatchPolicy, compute_patch_coverage, compute_total_coverage
from brimyr.detect import (
    CoverageFormat,
    Ecosystem,
    SonarStrategy,
    detect_ecosystems,
    ecosystem,
)
from brimyr.gate import (
    DEFAULT_MIN_LINES,
    DEFAULT_THRESHOLD,
    EXIT_ERROR,
    GateDecision,
    decide_gate,
)
from brimyr.local import resolve_local_base
from brimyr.modes import Mode, resolve_mode
from brimyr.runner import IngestError, RunResult, ingest_file, run_command, run_tests

_EXT_FORMAT = {
    ".info": CoverageFormat.LCOV,
    ".lcov": CoverageFormat.LCOV,
}

# `.xml` is deliberately NOT in the map above: it is not a format. Cobertura (coverage.py,
# coverlet, ReportGenerator) and JaCoCo (the whole JVM) both write `.xml`, and guessing
# wrong is SILENT rather than loud — the Cobertura parser finds no `<class filename=...>`
# in a JaCoCo file, returns an empty report, and every changed Java file then contributes
# nothing to the denominator. That reads as a comfortable pass over completely untested
# code. So `.xml` is resolved by looking at the root element instead.
_XML_HEAD_BYTES = 8192


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
    if fmt is None and path.suffix.lower() == ".xml":
        fmt = _sniff_xml_format(path)
    if fmt is None:
        raise ValueError(
            f"cannot infer coverage format for {path} — append ':lcov', ':cobertura' or ':jacoco'"
        )
    return path, fmt


def _sniff_xml_format(path: Path) -> CoverageFormat:
    """Tell JaCoCo XML from Cobertura XML by its root element.

    Reads only the head of the file — a multi-module JaCoCo report runs to megabytes
    and the root element is in the first line or two, after the XML declaration and
    the DOCTYPE that JaCoCo always emits.

    An unreadable or missing file resolves to Cobertura, which is not a guess so much
    as a deferral: `ingest_file` opens it moments later and raises the real
    "could not read" error, and that is a far better message than one about formats.
    """
    try:
        head = path.read_bytes()[:_XML_HEAD_BYTES].decode("utf-8", errors="replace")
    except OSError:
        return CoverageFormat.COBERTURA
    return CoverageFormat.JACOCO if is_jacoco(head) else CoverageFormat.COBERTURA


def _patch_policy(args: argparse.Namespace, extra_prefixes: tuple[str, ...] = ()) -> PatchPolicy:
    return PatchPolicy(
        strip_prefixes=tuple(args.strip_prefix or ()) + extra_prefixes,
        exclude_globs=tuple(getattr(args, "exclude", None) or ()),
    )


def counts_to_dict(decision: GateDecision) -> dict[str, object]:
    patch = decision.patch
    return {
        "patch_coverage": round(patch.percent, 2),
        "covered_lines": patch.covered_lines,
        "total_lines": patch.total_lines,
        "missing_lines": patch.missing_lines,
        "threshold": decision.threshold,
        # Deliberately NOT "total_lines" — that key already ships meaning the PATCH
        # denominator, and silently changing it would break every consumer.
        "total_coverage": (
            None
            if decision.total is None or not decision.total.measured
            else round(decision.total.percent, 2)
        ),
        "total_covered_lines": None if decision.total is None else decision.total.covered_lines,
        "total_executable_lines": (
            None if decision.total is None else decision.total.executable_lines
        ),
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
        if decision.below_min_lines:
            _eprint(
                f"brimyr: {decision.patch.total_lines} changed executable line(s) is below "
                f"the {decision.min_lines}-line minimum — threshold not applied"
            )
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
    # Empty string when nothing was measured, never "0.00" — an unmeasured run and a
    # genuinely zero-covered one must not look the same to a downstream `if`.
    if decision.total is not None:
        pairs["total_coverage"] = f"{decision.total.percent:.2f}" if decision.total.measured else ""
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

    policy = _patch_policy(args)
    patch = compute_patch_coverage(diff, report, policy)
    total = compute_total_coverage(report, policy)
    try:
        decision = decide_gate(
            patch, args.threshold, gate=not args.no_gate, total=total, min_lines=args.min_lines
        )
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
        if prop and outcome.coverage_paths:
            # EVERY report, not one. sonar.*.reportPaths is a comma-separated list, and a
            # multi-project solution produces one report per test project — sending only
            # the first understates coverage in SonarQube exactly as it did in the gate.
            sonar_paths[prop] = (
                *sonar_paths.get(prop, ()),
                *(str(path) for path in outcome.coverage_paths),
            )
        if outcome.error:
            _eprint(f"brimyr: {outcome.ecosystem.label}: {outcome.error}")
    return result.report, result.broken, ecosystems, sonar_paths


def _sonar_config(
    args: argparse.Namespace, sonar_paths: dict[str, tuple[str, ...]] | None = None
) -> sonar_mod.SonarConfig:
    return sonar_mod.SonarConfig(
        host_url=args.sonar_url,
        token=os.environ.get(args.sonar_token_env, ""),
        project_key=args.sonar_project_key or _default_project_key(),
        organization=args.sonar_organization,
        sources=args.sonar_sources,
        coverage_report_paths=sonar_paths or {},
        extra_args=tuple(args.sonar_arg or ()),
    )


def _default_project_key() -> str:
    """``owner/repo`` -> ``owner_repo``.

    Without a key `sonar-scanner` aborts, which is most of why the Sonar leg has never
    run for anyone. Sonar keys may not contain ``/``, so the slug cannot be used as-is.
    An explicit ``--sonar-project-key`` always wins.
    """
    return os.environ.get("GITHUB_REPOSITORY", "").replace("/", "_")


def _warn(message: str) -> None:
    """Annotate the run, not just the log.

    Plain stderr scrolls past in a green job and nobody sees it — which is exactly how
    "Sonar is wired up" stayed believable while nothing was ever uploaded. On Actions
    this surfaces on the summary page; elsewhere it is an ordinary stderr line.
    """
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else "brimyr: warning: "
    _eprint(f"{prefix}{message}")


def _missing_sonar_props(
    ecosystems: Sequence[Ecosystem], extra_args: Sequence[str]
) -> dict[str, tuple[str, ...]]:
    """Required Sonar properties an ecosystem needs that the caller did not supply."""
    # removeprefix, not lstrip: lstrip strips a character SET, so `/d:d.foo` would lose
    # its leading `d` as well and never match the required property name.
    supplied = {
        arg.split("=", 1)[0].removeprefix("-D").removeprefix("/d:").strip()
        for arg in extra_args
        if "=" in arg
    }
    missing: dict[str, tuple[str, ...]] = {}
    for eco in ecosystems:
        absent = tuple(p for p in eco.sonar_required_props if p not in supplied)
        if absent:
            missing[eco.label] = absent
    return missing


def _maybe_run_sonar(
    args: argparse.Namespace,
    sonar_paths: dict[str, tuple[str, ...]],
    ecosystems: Sequence[Ecosystem] = (),
) -> str | None:
    """Post-hoc `sonar-scanner` pass. Never raises; never changes the verdict."""
    if not args.sonar_url:
        return None
    if not os.environ.get(args.sonar_token_env, ""):
        _warn(
            f"SonarQube host is set but ${args.sonar_token_env} is empty — "
            "no analysis was uploaded."
        )
        return f"skipped (no token in ${args.sonar_token_env})"

    missing = _missing_sonar_props(ecosystems, args.sonar_arg or ())
    if missing:
        detail = "; ".join(f"{label} needs {', '.join(props)}" for label, props in missing.items())
        # Running anyway would not merely be useless: `sonar-scanner` over a Java repo
        # without sonar.java.binaries fails outright. A skip that says why beats a red
        # herring in the log.
        _warn(f"SonarQube analysis skipped — {detail} (pass it with --sonar-arg).")
        return f"skipped ({detail})"

    result = sonar_mod.run_scanner(_sonar_config(args, sonar_paths), args.repo)
    if not result.ok:
        _warn(f"SonarQube: {result.message}")
    return result.message


def _maybe_post_comment(args: argparse.Namespace, summary: str) -> str | None:
    """Post the PR comment when asked. Failure-isolated: never changes the verdict."""
    if not getattr(args, "pr_comment", False):
        return None

    slug = args.repo_slug or os.environ.get("GITHUB_REPOSITORY", "")
    number = args.pr_number if args.pr_number else _pr_number_from_event()
    if not number:
        return None
    token, byline = _comment_token(args, slug)
    config = comment_mod.CommentConfig(
        base_url=args.github_api_url
        or os.environ.get("GITHUB_API_URL")
        or "https://api.github.com",
        repo_slug=slug,
        pr_number=number or 0,
        token=token,
    )
    result = comment_mod.post_pr_comment(config, summary)
    return f"{result.message}{byline}"


def _comment_token(args: argparse.Namespace, slug: str) -> tuple[str, str]:
    """The token to comment with, and a suffix naming the identity it authors as.

    Tries the broker when one is configured and falls back to GITHUB_TOKEN on ANY
    failure — a broken broker costs a byline, never a comment and never a merge. The
    fallback is silent by design, which is what broker-smoke.yml exists to notice.
    """
    fallback = os.environ.get(args.github_token_env, "")
    broker_url = getattr(args, "token_broker_url", "") or ""
    if not broker_url:
        return fallback, ""

    owner, _, repo = slug.partition("/")
    result = broker_client.mint_bot_token(broker_url, owner, repo)
    if result.ok and result.token:
        return result.token, " as Brimyr[bot]"
    return fallback, f" as github-actions[bot] ({result.message})"


def _pr_number_from_event() -> int:
    """Read the PR number out of the Actions event payload, 0 when there isn't one."""
    path = os.environ.get("GITHUB_EVENT_PATH", "")
    if not path:
        return 0
    try:
        with open(path, encoding="utf-8") as handle:
            event = json.load(handle)
    except (OSError, ValueError):
        return 0
    number = (event.get("pull_request") or {}).get("number")
    return number if isinstance(number, int) else 0


def _wants_dotnet_scanner(args: argparse.Namespace) -> bool:
    """True when this run should wrap itself in `dotnet sonarscanner begin/end`.

    Requires an actual test run: the .NET analysis comes from Roslyn analyzers injected
    into a compilation, so with `--coverage-file` (no build, no test run) there is
    nothing for `end` to collect and the whole wrap would upload an empty analysis.
    """
    if not args.sonar_url or args.coverage_file:
        return False
    forced = [ecosystem(k) for k in (args.ecosystem or [])]
    detected = [e for e in forced if e] if args.ecosystem else detect_ecosystems(args.repo)
    return any(e.sonar_strategy is SonarStrategy.DOTNET for e in detected)


def _run_flow(args: argparse.Namespace, mode: Mode) -> int:
    # .NET is the one ecosystem whose scanner cannot follow the run — it has to wrap it,
    # because `end` collects analysis data the compilation produced. Everything else
    # stays a post-step.
    if _wants_dotnet_scanner(args):
        return _run_flow_wrapped(args, mode)
    return _run_flow_inner(args, mode, sonar=None)


def _run_flow_wrapped(args: argparse.Namespace, mode: Mode) -> int:
    globs = {
        eco.sonar_property: eco.sonar_report_globs
        for eco in detect_ecosystems(args.repo)
        if eco.sonar_property and eco.sonar_report_globs
    }
    with sonar_dotnet.session(_sonar_config(args), args.repo, report_globs=globs) as outcome:
        if outcome.skipped:
            _warn(f"SonarQube analysis skipped — {outcome.skipped}.")
        elif outcome.begin is not None and not outcome.begin.ok:
            _warn(f"SonarQube: {outcome.begin.message}")
        elif outcome.started:
            # The compile has to happen INSIDE the window, and it has to be a real one:
            # an incremental build compiles nothing, so the analyzers `begin` injected
            # never run and `end` finds no analysis data.
            _run_sonar_build(args)
        code = _run_flow_inner(args, mode, sonar=None)
    message = outcome.message
    if message and not outcome.ok:
        _warn(f"SonarQube: {message}")
    return code


def _run_sonar_build(args: argparse.Namespace) -> None:
    """Run each detected ecosystem's `sonar_build_command`. Never fails the gate."""
    for eco in detect_ecosystems(args.repo):
        if not eco.sonar_build_command:
            continue
        outcome = run_command(eco.sonar_build_command, args.repo)
        if not outcome.ok:
            _warn(
                f"SonarQube: `{' '.join(eco.sonar_build_command)}` failed "
                f"(exit {outcome.returncode}, non-blocking) — the analysis will be empty."
            )


def _run_flow_inner(args: argparse.Namespace, mode: Mode, *, sonar: None) -> int:
    del sonar
    collected = _collect_coverage(args)
    if isinstance(collected, int):
        return collected
    report, broken, ecosystems, sonar_paths = collected

    # One policy for both numbers. Total coverage has to honour the same exclude globs
    # as the patch gate, or a PR comment shows two figures disagreeing by twenty points
    # because one of them still counts the EF migrations.
    repo_abs = str(Path(args.repo).resolve())
    policy = _patch_policy(args, (repo_abs,))

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
        patch = compute_patch_coverage(diff, report, policy)
    else:
        patch = compute_patch_coverage(DiffIndex(()), report)

    try:
        total = compute_total_coverage(report, policy)
        decision = decide_gate(
            patch,
            args.threshold,
            broken=broken,
            gate=mode.gates,
            total=total,
            min_lines=args.min_lines,
        )
    except ValueError as exc:
        return _fail(str(exc))

    wrapped = any(e.sonar_strategy is SonarStrategy.DOTNET for e in ecosystems)
    # The .NET scanner analyses the whole tree; a second plain `sonar-scanner` run
    # against the same projectKey would overwrite what it just uploaded.
    sonar_message = None if (broken or wrapped) else _maybe_run_sonar(args, sonar_paths, ecosystems)

    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(counts_to_dict(decision), indent=2), encoding="utf-8"
        )

    summary = report_mod.render_summary(
        decision, mode, broken=broken, ecosystems=ecosystems, sonar_message=sonar_message
    )
    report_mod.append_step_summary(summary)
    comment_message = _maybe_post_comment(args, summary)
    if not args.quiet:
        if comment_message:
            _eprint(f"brimyr: {comment_message}")
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
        "--min-lines",
        type=int,
        default=DEFAULT_MIN_LINES,
        help=(
            "Do not gate a diff with fewer than N changed executable lines — the "
            f"percentage is too coarse to mean anything (default: {DEFAULT_MIN_LINES}, "
            "matching SonarQube). 0 gates every diff."
        ),
    )
    parser.add_argument(
        "--strip-prefix",
        action="append",
        metavar="PREFIX",
        help="Path prefix to strip from coverage paths before matching (repeatable).",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        metavar="GLOB",
        help=(
            "Drop changed files matching this glob from the patch-coverage denominator "
            "entirely — for generated code (repeatable). e.g. '*Migrations*'."
        ),
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


def _add_comment_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--pr-comment",
        action="store_true",
        help="Post/update one patch-coverage comment on the PR (non-blocking).",
    )
    parser.add_argument(
        "--github-token-env",
        default="GITHUB_TOKEN",
        help="Env var holding the token used to comment (default: GITHUB_TOKEN).",
    )
    parser.add_argument("--repo-slug", help="owner/repo (default: $GITHUB_REPOSITORY).")
    parser.add_argument("--pr-number", type=int, help="PR number (default: from the event).")
    parser.add_argument(
        "--token-broker-url",
        default="",
        help=(
            "Token broker base URL. When set, the comment is authored by Brimyr[bot] "
            "instead of github-actions[bot]. Falls back silently if the mint fails."
        ),
    )
    parser.add_argument(
        "--github-api-url",
        default="",
        help="GitHub API base URL (default: $GITHUB_API_URL, else api.github.com).",
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
        help=(
            "Force an ecosystem (python|javascript|dotnet|java) instead of auto-detect. Repeatable."
        ),
    )
    ci.add_argument(
        "--test-command",
        help="Override the detected test command (a shell command string).",
    )
    _add_sonar_args(ci)
    _add_comment_args(ci)
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
    _add_comment_args(local)
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
