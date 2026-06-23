"""Run ``sonar-scanner`` to ship quality + coverage to SonarQube — non-blocking.

SonarQube is the **only** Sonar run in the pipeline and it is owned here. Unlike a
findings aggregator you POST to, Sonar is an *analyzer you drive*: this runs
``sonar-scanner``, which performs Sonar's native quality analysis **and** reads the
coverage file(s) the test run already produced, uploading both in one pass. Sonar
derives new-vs-old code itself (its New Code Period), so we never feed it
"net-new" — that is the patch gate's job, computed locally.

By contract this is **failure-isolated**: :func:`run_scanner` never raises and the
gate never depends on it. A Sonar outage, a missing scanner binary, or a bad URL
all return ``ok=False`` and the run continues. The token is passed via the
``SONAR_TOKEN`` env var, never on argv, so it can't leak into a process listing.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path

# A runner takes (argv, cwd, env) and returns the completed process.
Runner = Callable[[list[str], str, dict[str, str]], subprocess.CompletedProcess]


@dataclass(frozen=True)
class SonarConfig:
    host_url: str
    token: str
    project_key: str | None = None
    organization: str | None = None
    sources: str = "."
    scanner_bin: str = "sonar-scanner"
    # Map of Sonar coverage property -> coverage file paths (comma-joined on argv).
    coverage_report_paths: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    extra_args: tuple[str, ...] = ()


@dataclass(frozen=True)
class SonarResult:
    ok: bool
    message: str
    returncode: int | None = None
    command: tuple[str, ...] = ()


def build_scanner_args(config: SonarConfig) -> list[str]:
    """Assemble the ``sonar-scanner`` argv (token is NOT included — it goes in env)."""
    args = [config.scanner_bin, f"-Dsonar.host.url={config.host_url}"]
    if config.project_key:
        args.append(f"-Dsonar.projectKey={config.project_key}")
    if config.organization:
        args.append(f"-Dsonar.organization={config.organization}")
    if config.sources:
        args.append(f"-Dsonar.sources={config.sources}")
    for prop, paths in config.coverage_report_paths.items():
        if prop and paths:
            args.append(f"-D{prop}={','.join(str(p) for p in paths)}")
    args.extend(config.extra_args)
    return args


def _default_runner(argv: list[str], cwd: str, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(argv, cwd=cwd, env=env, check=False, capture_output=True, text=True)


def run_scanner(
    config: SonarConfig,
    repo: str | Path = ".",
    *,
    base_env: Mapping[str, str] | None = None,
    runner: Runner | None = None,
) -> SonarResult:
    """Run ``sonar-scanner``. Never raises — returns a :class:`SonarResult`."""
    if not config.host_url:
        return SonarResult(False, "skipped (no SonarQube host URL set)")
    if not config.token:
        return SonarResult(False, "skipped (no SonarQube token set)")

    env = dict(base_env if base_env is not None else os.environ)
    env["SONAR_TOKEN"] = config.token
    argv = build_scanner_args(config)
    run_fn = runner or _default_runner

    try:
        completed = run_fn(argv, str(repo), env)
    except FileNotFoundError:
        return SonarResult(
            False,
            f"skipped ({config.scanner_bin} not found on PATH)",
            command=tuple(argv),
        )
    except OSError as exc:
        return SonarResult(False, f"could not run sonar-scanner: {exc}", command=tuple(argv))

    if completed.returncode == 0:
        return SonarResult(True, "analysis uploaded", returncode=0, command=tuple(argv))
    detail = (getattr(completed, "stderr", "") or getattr(completed, "stdout", "") or "").strip()
    return SonarResult(
        False,
        f"sonar-scanner failed (exit {completed.returncode}, non-blocking): {detail[:300]}",
        returncode=completed.returncode,
        command=tuple(argv),
    )
