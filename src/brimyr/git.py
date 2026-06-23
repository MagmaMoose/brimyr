"""The git IO boundary for patch coverage.

Everything that shells out to ``git`` lives here so :mod:`brimyr.coverage` stays
pure and unit-testable. The job is small: resolve the merge-base of the PR's base
and head, produce ``git diff --unified=0`` text, and fail *loudly and actionably*
when history is missing (shallow clone / unrelated histories) — the most common
real-world cause being a checkout without ``fetch-depth: 0``.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from brimyr.coverage.diff import DiffIndex, parse_unified_diff

_FETCH_DEPTH_HINT = (
    "Set `fetch-depth: 0` on actions/checkout (or `git fetch --unshallow`) so the "
    "full history and the base..head merge-base are available."
)


class GitError(RuntimeError):
    """A git invocation failed."""


class MergeBaseError(GitError):
    """No merge-base could be determined for the given base and head."""


class ShallowCloneError(MergeBaseError):
    """History is shallow, so the merge-base is unavailable."""


def _git(args: list[str], cwd: str | Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
        check=False,
    )


def _git_out(args: list[str], cwd: str | Path | None = None) -> str:
    proc = _git(args, cwd)
    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed (exit {proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def is_shallow(cwd: str | Path | None = None) -> bool:
    """True if the working tree is a shallow clone."""
    proc = _git(["rev-parse", "--is-shallow-repository"], cwd)
    return proc.returncode == 0 and proc.stdout.strip() == "true"


def merge_base(base: str, head: str, cwd: str | Path | None = None) -> str:
    """Resolve the merge-base SHA of ``base`` and ``head``.

    Raises :class:`ShallowCloneError` when the clone is shallow, or
    :class:`MergeBaseError` otherwise (bad ref / unrelated histories), each with
    an actionable message.
    """
    proc = _git(["merge-base", base, head], cwd)
    out = proc.stdout.strip()
    if proc.returncode == 0 and out:
        return out.splitlines()[0].strip()

    detail = proc.stderr.strip() or "no common ancestor found"
    if is_shallow(cwd):
        raise ShallowCloneError(
            f"Cannot find the merge-base of {base!r} and {head!r}: the checkout is "
            f"shallow. {_FETCH_DEPTH_HINT} (git said: {detail})"
        )
    raise MergeBaseError(
        f"Cannot find the merge-base of {base!r} and {head!r}: {detail}. This usually "
        f"means a shallow checkout or unrelated histories. {_FETCH_DEPTH_HINT}"
    )


def diff_text(base_rev: str, head: str, cwd: str | Path | None = None) -> str:
    """``git diff --unified=0`` text (rename/copy aware) from ``base_rev`` to ``head``."""
    return _git_out(
        [
            "-c",
            "core.quotepath=false",
            "diff",
            "--unified=0",
            "--no-color",
            "--find-renames",
            "--find-copies",
            f"{base_rev}..{head}",
        ],
        cwd,
    )


def compute_changed_lines(
    base: str,
    head: str,
    cwd: str | Path | None = None,
    *,
    use_merge_base: bool = True,
) -> DiffIndex:
    """Changed files + added line ranges introduced by ``head`` relative to ``base``.

    With ``use_merge_base`` (default), diffs ``merge-base(base, head)..head`` — the
    set of changes the PR actually introduces — which is also robust to rebases
    and force-pushes of the base branch.
    """
    base_rev = merge_base(base, head, cwd) if use_merge_base else base
    return parse_unified_diff(diff_text(base_rev, head, cwd))
