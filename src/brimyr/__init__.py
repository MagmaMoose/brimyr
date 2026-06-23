"""Brimyr — a patch-coverage gate.

Auto-detects the repo's ecosystem, runs the right test command with coverage
instrumentation on, then gates a pull request on the coverage of *the lines the
PR changed* (diff-cover style). Non-blocking, it also runs ``sonar-scanner`` to
ship quality + coverage to SonarQube for the trend.
"""

from __future__ import annotations

__version__ = "1.0.0"
