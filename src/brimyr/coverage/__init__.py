"""The pure patch-coverage core — deterministic, no I/O, heavily tested.

This package takes already-parsed data (unified-diff text + a coverage report)
and computes patch coverage: the fraction of *executable lines the PR changed*
that the test run covered. Everything here is pure; the git/subprocess boundary
lives in :mod:`brimyr.git` and the test-runner boundary in :mod:`brimyr.runner`.
"""
