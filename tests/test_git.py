"""Integration tests for the git IO boundary against real repositories.

Ported from chargate's `tests/test_git.py`: the two projects' `git.py` are
near-byte-identical, so the same failure modes apply and the same tests should hold.
Keeping them in step is deliberate — see `tests/fixtures/diff_corpus/` for the
shared-fixture half of the same idea.

These exercise the parser end-to-end on real `git diff` output rather than synthetic
strings, and pin the two failure modes that produce a *wrong number* rather than a
crash: a missing merge-base and a shallow clone. Both must be loud (exit 2), because a
diff that silently comes back empty makes every changed line vanish from the denominator
and the gate reports a comfortable pass over untested code.
"""

from __future__ import annotations

import subprocess  # nosec B404 - these tests drive a real git repository on purpose
from pathlib import Path

import pytest

from brimyr import git as bgit
from brimyr.coverage.diff import DiffIndex
from brimyr.coverage.model import CoverageReport, FileCoverage
from brimyr.coverage.patch import compute_patch_coverage

pytestmark = pytest.mark.skipif(
    subprocess.run(["git", "--version"], capture_output=True).returncode != 0,  # nosec B607
    reason="git is not available",
)


def _run(args: list[str], cwd: Path) -> None:
    subprocess.run(  # nosec B607 - fixed argv, no shell
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    )


def _init_repo(path: Path) -> None:
    _run(["init", "-q", "-b", "main"], path)
    _run(["config", "user.email", "test@example.com"], path)
    _run(["config", "user.name", "Test"], path)
    _run(["config", "commit.gpgsign", "false"], path)


def _commit_all(path: Path, message: str) -> str:
    _run(["add", "-A"], path)
    _run(["commit", "-q", "-m", message], path)
    return subprocess.run(  # nosec B607 - fixed argv, no shell
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True, check=True
    ).stdout.strip()


def test_end_to_end_patch_coverage_against_a_real_repo(tmp_path: Path):
    """Pre-existing uncovered lines must not count; only the added one does."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)

    src = repo / "app.py"
    src.write_text("line1 = 1\nline2 = 2\nline3 = 3\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")

    src.write_text("line1 = 1\nline2 = 2\nline3 = 3\nnew_line = 4\n", encoding="utf-8")
    head_sha = _commit_all(repo, "pr")

    diff_index = bgit.compute_changed_lines(base_sha, head_sha, repo)
    assert isinstance(diff_index, DiffIndex)  # nosec B101
    app = diff_index.get("app.py")
    assert app is not None  # nosec B101
    assert app.contains_line(4)  # nosec B101
    assert not app.contains_line(1)  # nosec B101

    # Lines 1-3 uncovered (pre-existing debt), line 4 covered.
    report = CoverageReport((FileCoverage("app.py", frozenset({4}), frozenset({1, 2, 3})),))
    patch = compute_patch_coverage(diff_index, report)
    assert patch.total_lines == 1  # nosec B101 - only the added line is in scope
    assert patch.covered_lines == 1  # nosec B101
    assert patch.percent == 100.0  # nosec B101 - the legacy debt does not drag it down


def test_a_new_file_is_entirely_in_scope(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "keep.py").write_text("x = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "brand_new.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    head_sha = _commit_all(repo, "add file")

    diff_index = bgit.compute_changed_lines(base_sha, head_sha, repo)
    new_file = diff_index.get("brand_new.py")
    assert new_file is not None  # nosec B101
    assert new_file.is_new_file  # nosec B101

    report = CoverageReport((FileCoverage("brand_new.py", frozenset(), frozenset({1, 2})),))
    patch = compute_patch_coverage(diff_index, report)
    assert patch.total_lines == 2  # nosec B101
    assert patch.covered_lines == 0  # nosec B101


def test_a_deleted_file_contributes_nothing(tmp_path: Path):
    """Deleting code is not an untested change; it must not fail a gate."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "gone.py").write_text("a = 1\nb = 2\n", encoding="utf-8")
    (repo / "keep.py").write_text("x = 1\n", encoding="utf-8")
    base_sha = _commit_all(repo, "base")
    (repo / "gone.py").unlink()
    head_sha = _commit_all(repo, "delete file")

    diff_index = bgit.compute_changed_lines(base_sha, head_sha, repo)
    report = CoverageReport((FileCoverage("gone.py", frozenset(), frozenset({1, 2})),))
    patch = compute_patch_coverage(diff_index, report)
    assert patch.total_lines == 0  # nosec B101


def test_a_missing_merge_base_raises_an_actionable_error(tmp_path: Path):
    """Loud, not empty. An empty diff would be a silent vacuous pass."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    head_sha = _commit_all(repo, "main commit")

    # An orphan branch shares no history, so there is no merge-base.
    _run(["checkout", "-q", "--orphan", "unrelated"], repo)
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    orphan_sha = _commit_all(repo, "orphan commit")

    with pytest.raises(bgit.MergeBaseError) as exc:
        bgit.compute_changed_lines(head_sha, orphan_sha, repo)
    assert "fetch-depth: 0" in str(exc.value)  # nosec B101 - names the fix, not just the fault


def test_a_shallow_clone_raises_the_specific_error(tmp_path: Path, monkeypatch):
    """`actions/checkout` defaults to depth 1, so this is the common misconfiguration."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    head_sha = _commit_all(repo, "main commit")
    _run(["checkout", "-q", "--orphan", "unrelated"], repo)
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    orphan_sha = _commit_all(repo, "orphan commit")

    # Force the shallow path: merge-base fails AND the repo reports shallow.
    monkeypatch.setattr(bgit, "is_shallow", lambda cwd=None: True)
    with pytest.raises(bgit.ShallowCloneError) as exc:
        bgit.merge_base(head_sha, orphan_sha, repo)
    assert "shallow" in str(exc.value).lower()  # nosec B101
    assert "fetch-depth: 0" in str(exc.value)  # nosec B101


def test_no_merge_base_still_diffs_when_asked(tmp_path: Path):
    """`--no-merge-base` is the escape hatch; it must not need a common ancestor."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    (repo / "a.py").write_text("a = 1\n", encoding="utf-8")
    head_sha = _commit_all(repo, "main commit")
    _run(["checkout", "-q", "--orphan", "unrelated"], repo)
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    orphan_sha = _commit_all(repo, "orphan commit")

    index = bgit.compute_changed_lines(head_sha, orphan_sha, repo, use_merge_base=False)
    assert index.get("b.py") is not None  # nosec B101
