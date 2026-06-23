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
