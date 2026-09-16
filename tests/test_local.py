"""Unit tests for local base resolution (brimyr.local)."""

from __future__ import annotations

import subprocess

import pytest

from brimyr.local import resolve_local_base


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@e.com",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@e.com",
            "PATH": _path(),
        },
    )


def _path():
    import os

    return os.environ.get("PATH", "")


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "main")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-q", "-m", "c")
    return tmp_path


def test_explicit_wins(repo):
    assert resolve_local_base(repo, "feature/x") == "feature/x"


def test_falls_back_to_main(repo):
    assert resolve_local_base(repo) == "main"


def test_none_when_no_base(tmp_path):
    _git(tmp_path, "init", "-q", "-b", "wip")
    (tmp_path / "f").write_text("x")
    _git(tmp_path, "add", "f")
    _git(tmp_path, "commit", "-q", "-m", "c")
    assert resolve_local_base(tmp_path) is None


def test_a_git_that_cannot_run_is_not_reported_as_a_missing_base_branch(monkeypatch, tmp_path):
    """`None` means git ran and found no base branch. This is git never running.

    Collapsing the second into the first produced "could not infer a base branch --
    pass --base explicitly", which is advice that cannot help: passing a base does not
    install git, and it does not create a directory that is not there.
    """
    import subprocess as sp

    from brimyr.git import GitError

    def no_git(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory", "git")

    monkeypatch.setattr(sp, "run", no_git)
    with pytest.raises(GitError) as exc_info:
        resolve_local_base(tmp_path)
    assert "could not run" in str(exc_info.value)


def test_an_explicit_base_never_shells_out(monkeypatch, tmp_path):
    """The explicit branch returns before any subprocess, so a broken git must not stop
    a developer who already said what to diff against."""
    import subprocess as sp

    def explode(*args, **kwargs):
        raise AssertionError("resolve_local_base shelled out for an explicit base")

    monkeypatch.setattr(sp, "run", explode)
    assert resolve_local_base(tmp_path, "origin/main") == "origin/main"
