"""Brimyr — quality assurance for a pull request, in two halves.

**Patch coverage.** Auto-detects the repo's ecosystem, runs the right test command
with coverage instrumentation on, then gates on the coverage of *the lines the PR
changed* (diff-cover style).

**Net-new quality findings.** Optionally (``quality: true``) runs Chargate as a nested
step and gates on the findings the PR introduced — see :mod:`brimyr.quality`. Both
verdicts land in one job summary and one PR comment, and the process exits with the
worse of the two.

Non-blocking, it also runs ``sonar-scanner`` to ship quality + coverage to SonarQube
for the trend.

Brimyr is quality assurance; Chargate is security assurance. The boundary is the
subject, not the tool — brimyr's quality half calls chargate's engine.
"""

from __future__ import annotations

__version__ = "1.9.4"
