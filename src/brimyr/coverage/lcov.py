"""Parse LCOV ``.info`` coverage into a :class:`CoverageReport`.

LCOV (emitted by ``jest --coverage``, ``c8``, ``nyc``, gcov, …) is a flat,
record-per-file text format. Only two directives matter for line coverage:

* ``SF:<path>`` — start of a file's record (the source path).
* ``DA:<line>,<hits>[,<checksum>]`` — a line's execution count.

Everything else (functions ``FN``/``FNDA``, branches ``BRDA``, the ``LF``/``LH``
summaries) is ignored: patch coverage is a *line* metric. ``end_of_record`` closes
the current file. This module is **pure** — it parses text, touches no files.
"""

from __future__ import annotations

from brimyr.coverage.model import CoverageBuilder, CoverageReport


def parse_lcov(text: str) -> CoverageReport:
    """Parse LCOV ``.info`` text into a :class:`CoverageReport`."""
    builder = CoverageBuilder()
    current: str | None = None

    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("SF:"):
            current = line[3:].strip()
        elif line == "end_of_record":
            current = None
        elif line.startswith("DA:") and current is not None:
            payload = line[3:].split(",")
            if len(payload) < 2:
                continue
            try:
                lineno = int(payload[0])
                hits = int(payload[1])
            except ValueError:
                continue
            builder.record(current, lineno, hits)

    return builder.build()
