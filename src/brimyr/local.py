"""Local entrypoint helpers — patch coverage for the branch you're about to push.

``brimyr local`` runs the same detect → test → patch-coverage flow as CI, but
against a *locally inferred* base so a developer can check their branch before
pushing. The only extra job here is picking that base sensibly: an explicit ref
wins, otherwise the repo's default branch (``origin/HEAD``), falling back to a
local ``main``/``master``. This is the one bit of git guesswork that doesn't
belong in the pure core, so it lives here next to :mod:`brimyr.git`.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

_FALLBACK_BASES = ("main", "master", "origin/main", "origin/master")


def _git_out(args: list[str], repo: str | Path) -> str | None:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        return None
    return proc.stdout.strip()


def _rev_exists(ref: str, repo: str | Path) -> bool:
    return _git_out(["rev-parse", "--verify", "--quiet", ref], repo) not in (None, "")


def resolve_local_base(repo: str | Path = ".", explicit: str | None = None) -> str | None:
    """Pick a base ref for a local run: explicit, else default branch, else fallback.

    Returns ``None`` when nothing usable is found (the CLI then reports that there
    is nothing to diff against rather than failing loudly).
    """
    if explicit:
        return explicit

    # The remote's default branch, e.g. "origin/main", if the symref is configured.
    head = _git_out(["symbolic-ref", "--short", "refs/remotes/origin/HEAD"], repo)
    if head and _rev_exists(head, repo):
        return head

    for candidate in _FALLBACK_BASES:
        if _rev_exists(candidate, repo):
            return candidate
    return None
