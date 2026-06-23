"""Parse unified-diff text into a structured, queryable index of changes.

This module is **pure**: it takes the text of ``git diff --unified=0`` (with
rename/copy detection enabled) and returns a :class:`DiffIndex` describing, per
file, its change status and the set of line ranges added/modified on the *new*
(head) side. The line ranges come straight from the hunk headers
(``@@ -a,b +c,d @@`` → new-side range ``c .. c+d-1``), so we never have to read
hunk bodies.

These are the lines a PR introduced; patch coverage asks what fraction of the
*executable* ones among them the test run covered. The git invocation that
produces this text lives in :mod:`brimyr.git`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

FileStatus = Literal["added", "modified", "deleted", "renamed", "copied"]

# New-side of the hunk header: @@ -<old_start>[,<old_len>] +<new_start>[,<new_len>] @@
_HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def normalize_path(path: str) -> str:
    """Normalize a diff/coverage path: unquote, forward slashes, strip leading ``./``."""
    p = path.strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        # git quotes paths containing special chars when core.quotepath is on.
        p = p[1:-1]
    p = p.replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def _strip_ab_prefix(path: str) -> str:
    """Strip a leading ``a/`` or ``b/`` diff prefix from a ``---``/``+++`` path."""
    p = path.strip()
    if len(p) >= 2 and p[0] == '"' and p[-1] == '"':
        p = p[1:-1]
    if p[:2] in ("a/", "b/"):
        p = p[2:]
    return normalize_path(p)


@dataclass(frozen=True)
class FileDiff:
    """One file's change in a diff.

    ``path`` is the *head* (new) path — the one a coverage report references.
    For deleted files it is the old path (deleted files are dropped downstream).
    ``added_ranges`` are inclusive ``(start, end)`` line ranges on the new side.
    """

    path: str
    status: FileStatus
    added_ranges: tuple[tuple[int, int], ...] = ()
    old_path: str | None = None

    @property
    def is_new_file(self) -> bool:
        return self.status == "added"

    @property
    def is_deleted(self) -> bool:
        return self.status == "deleted"

    @property
    def has_line_info(self) -> bool:
        return bool(self.added_ranges)

    def contains_line(self, line: int) -> bool:
        return any(start <= line <= end for start, end in self.added_ranges)

    def added_lines(self) -> frozenset[int]:
        """The flat set of new-side line numbers this file added/modified."""
        lines: set[int] = set()
        for start, end in self.added_ranges:
            lines.update(range(start, end + 1))
        return frozenset(lines)


@dataclass(frozen=True)
class DiffIndex:
    """All files changed between two commits, keyed by normalized head path."""

    files: tuple[FileDiff, ...]

    def as_dict(self) -> dict[str, FileDiff]:
        """Map normalized head path -> FileDiff (last one wins on duplicates)."""
        return {f.path: f for f in self.files}

    def get(self, path: str) -> FileDiff | None:
        norm = normalize_path(path)
        for f in self.files:
            if f.path == norm:
                return f
        return None

    def __bool__(self) -> bool:
        return bool(self.files)

    def __len__(self) -> int:
        return len(self.files)


class _Block:
    """Mutable accumulator for the diff section of a single file."""

    __slots__ = ("new_path", "old_path", "ranges", "status")

    def __init__(self) -> None:
        self.old_path: str | None = None
        self.new_path: str | None = None
        self.status: FileStatus | None = None
        self.ranges: list[tuple[int, int]] = []

    def to_file_diff(self) -> FileDiff | None:
        status: FileStatus = self.status or "modified"
        if status == "deleted":
            path = self.old_path or self.new_path
        else:
            path = self.new_path or self.old_path
        if path is None:
            return None
        old = self.old_path if status in ("renamed", "copied") and self.old_path != path else None
        return FileDiff(
            path=path,
            status=status,
            added_ranges=tuple(self.ranges),
            old_path=old,
        )


def parse_unified_diff(text: str) -> DiffIndex:
    """Parse ``git diff --unified=0 -M -C`` output into a :class:`DiffIndex`."""
    files: list[FileDiff] = []
    block: _Block | None = None

    def flush() -> None:
        nonlocal block
        if block is not None:
            fd = block.to_file_diff()
            if fd is not None:
                files.append(fd)
        block = None

    for raw in text.splitlines():
        if raw.startswith("diff --git "):
            flush()
            block = _Block()
            continue
        if block is None:
            continue

        if raw.startswith("new file mode"):
            block.status = "added"
        elif raw.startswith("deleted file mode"):
            block.status = "deleted"
        elif raw.startswith("rename from "):
            block.old_path = normalize_path(raw[len("rename from ") :])
            if block.status is None:
                block.status = "renamed"
        elif raw.startswith("rename to "):
            block.new_path = normalize_path(raw[len("rename to ") :])
            block.status = "renamed"
        elif raw.startswith("copy from "):
            block.old_path = normalize_path(raw[len("copy from ") :])
            if block.status is None:
                block.status = "copied"
        elif raw.startswith("copy to "):
            block.new_path = normalize_path(raw[len("copy to ") :])
            block.status = "copied"
        elif raw.startswith("--- "):
            p = raw[4:].strip()
            if p != "/dev/null":
                block.old_path = _strip_ab_prefix(p)
        elif raw.startswith("+++ "):
            p = raw[4:].strip()
            if p == "/dev/null":
                block.status = "deleted"
            else:
                block.new_path = _strip_ab_prefix(p)
        else:
            m = _HUNK_RE.match(raw)
            if m:
                start = int(m.group(1))
                count = 1 if m.group(2) is None else int(m.group(2))
                if count > 0:
                    block.ranges.append((start, start + count - 1))

    flush()
    return DiffIndex(tuple(files))
