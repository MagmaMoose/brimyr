"""Parse a Go coverage profile (``go test -coverprofile``) into a :class:`CoverageReport`.

Go has no line-coverage format of its own. ``go test -coverprofile=coverage.out``
writes one record per *basic block*::

    mode: set
    example.com/m/pkg/file.go:4.24,7.13 1 1
    example.com/m/pkg/file.go:7.13,9.3 1 0

``<file>:<startLine>.<startCol>,<endLine>.<endCol> <numStmt> <count>``: a span of
source, how many statements it holds, and how often it ran. Three things follow:

* **The path is an import path, not a file path.** It starts with the module path
  from ``go.mod`` (``example.com/m``), which the diff never carries. Mapping it back
  onto the checkout needs the filesystem, so it is injected (``resolve_path``) exactly
  as :mod:`brimyr.coverage.jacoco` injects its module-root resolver. Anything that
  does not resolve is kept as-is, where the suffix match in
  :mod:`brimyr.coverage.patch` still gets its chance.
* **A block is a span, and its edges are braces.** A function's first block starts
  AT its opening ``{`` (on the ``func`` line) and an ``if`` body's block ends just
  past its closing ``}``. Expanding every span to whole lines counts the ``func``
  signature, every lone ``}`` and every blank or comment line inside a body as an
  executable line: measured on MagmaMoose/cloudnative-vk, 16% of the lines inside
  blocks were a lone closing brace. With the source available (``source_lines``) the
  span is trimmed past braces and whitespace at both ends and blank and ``//`` lines
  are dropped, which is what makes the result a statement metric again. On a block
  structure like ``if`` + body + ``return`` the trimmed line count reproduces the
  percentage ``go test -cover`` itself prints; the untrimmed one does not.
* **A zero-statement block is not code.** ``func Empty() {}`` still gets a block, with
  ``numStmt`` 0. It is skipped.

Packages with no test files are still in the profile, every block at count 0 (Go
1.22+), so untested code lands in the denominator as uncovered rather than vanishing.
A line two blocks share (``} else {``) is covered if either ran: covered-wins, as
everywhere else. **Pure**: parses a string; the two callables are the only way in.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

from brimyr.coverage.model import CoverageBuilder, CoverageReport

#: The first line of every Go coverage profile. `go test` writes exactly one; tools that
#: concatenate profiles leave one per input, so every such line is skipped, not just the
#: first.
_MODE_PREFIX = "mode:"
_MODES = frozenset({"set", "count", "atomic"})


class GoCoverError(ValueError):
    """The text is not a Go coverage profile."""


def is_gocover(text: str) -> bool:
    """True if ``text`` opens like a Go coverage profile (``mode: set|count|atomic``)."""
    for line in text.splitlines():
        if line.strip():
            head, _, mode = line.strip().partition(":")
            return head == "mode" and mode.strip() in _MODES
    return False


def _edge(char: str) -> bool:
    """A character a block may start or end on without it being a statement."""
    return char.isspace() or char in "{}"


def _first_code_line(lines: Sequence[str], line: int, col: int, last: int) -> int | None:
    """The first line at or after ``line``.``col`` holding something other than a brace.

    A ``//`` ends the line for this purpose: the rest is a comment, not the block's
    first statement. ``None`` when nothing but braces and whitespace is left before
    ``last``, i.e. the block is empty.
    """
    while line <= last:
        text = lines[line - 1]
        while col <= len(text):
            if text.startswith("//", col - 1):
                break
            if not _edge(text[col - 1]):
                return line
            col += 1
        line, col = line + 1, 1
    return None


def _last_code_line(lines: Sequence[str], line: int, col: int, first: int) -> int:
    """The last line at or before ``line``.``col`` holding something other than a brace.

    Never earlier than ``first``: the start is already known to hold code, so the
    walk back always stops there.
    """
    while line > first:
        text = lines[line - 1]
        col = min(col, len(text))
        while col >= 1:
            if not _edge(text[col - 1]):
                return line
            col -= 1
        line -= 1
        col = len(lines[line - 1])
    return first


def _is_code(text: str) -> bool:
    stripped = text.strip()
    return bool(stripped) and not stripped.startswith("//")


def block_lines(
    start: tuple[int, int], end: tuple[int, int], lines: Sequence[str] | None
) -> tuple[int, ...]:
    """The line numbers a block's statements occupy.

    ``start`` and ``end`` are the profile's ``(line, column)`` pairs, 1-based, the end
    column exclusive. Without ``lines`` (the source was not found) the whole span is
    returned: over-counting braces is the lesser failure next to dropping the file.
    A span that runs off the end of the source means the file is not the one that was
    measured, and is treated the same way.
    """
    first, last = start[0], end[0]
    if lines is None or not (1 <= first <= last <= len(lines)):
        return tuple(range(first, last + 1))
    code_start = _first_code_line(lines, first, start[1], last)
    if code_start is None:
        return ()
    code_end = _last_code_line(lines, last, end[1] - 1, code_start)
    return tuple(n for n in range(code_start, code_end + 1) if _is_code(lines[n - 1]))


def _position(text: str) -> tuple[int, int]:
    line, _, col = text.partition(".")
    return int(line), int(col)


def parse_gocover(
    text: str,
    *,
    resolve_path: Callable[[str], str] | None = None,
    source_lines: Callable[[str], Sequence[str] | None] | None = None,
) -> CoverageReport:
    """Parse a Go coverage profile into a :class:`CoverageReport`.

    ``resolve_path`` maps a profile path (an import path) to the path to record;
    ``source_lines`` returns the source of a RESOLVED path as a list of lines, or
    ``None``. Both default to "not available", which records import paths over
    untrimmed spans. Raises :class:`GoCoverError` when the text is not a profile at
    all: a file that is not what it claims to be is a broken input, never an empty
    report that passes.
    """
    if not is_gocover(text):
        raise GoCoverError("not a Go coverage profile (expected a first line `mode: set`)")

    resolve = resolve_path or (lambda path: path)
    builder = CoverageBuilder()
    resolved: dict[str, str] = {}
    sources: dict[str, Sequence[str] | None] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(_MODE_PREFIX):
            continue
        # rpartition: the span is after the LAST colon; a Windows path has one of its own.
        name, _, rest = line.rpartition(":")
        fields = rest.split()
        if not name or len(fields) != 3:
            continue
        span, statements, count = fields
        start_text, _, end_text = span.partition(",")
        try:
            start, end = _position(start_text), _position(end_text)
            n_statements, hits = int(statements), int(count)
        except ValueError:
            continue
        if n_statements == 0:
            continue
        if name not in resolved:
            resolved[name] = resolve(name)
            sources[name] = source_lines(resolved[name]) if source_lines else None
        for number in block_lines(start, end, sources[name]):
            builder.record(resolved[name], number, hits)
    return builder.build()
