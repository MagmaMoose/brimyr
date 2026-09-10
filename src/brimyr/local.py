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

from brimyr.git import GitError

_FALLBACK_BASES = ("main", "master", "origin/main", "origin/master")


def _git_out(args: list[str], repo: str | Path) -> str | None:
    """Git's stdout, or ``None`` when git ran and said no.

    "git ran and returned non-zero" and "git could not run at all" are different
    answers and only the first one means "no base branch here". Letting the second
    collapse into ``None`` made a missing git binary, or a `--repo` that does not
    exist, come back as "could not infer a base branch — pass --base explicitly",
    which is advice that cannot help: passing a base does not install git. So an
    `OSError` is raised as a :class:`~brimyr.git.GitError` for the CLI to report as
    the setup error it is, and ``None`` keeps its one narrow meaning.
    """
    try:
        # Same as `brimyr.git._git`: a list argv (no shell), and "git" from PATH.
        proc = subprocess.run(  # nosec B603 B607
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise GitError(
            f"could not run `git {' '.join(args)}` in {repo}: {exc}. Check that git is "
            "installed and that the repository path exists."
        ) from exc
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
