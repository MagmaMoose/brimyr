"""An entirely empty coverage report is a broken run, not a vacuous pass.

A report that parses but names ZERO files is not "0% coverage" and it is not "nothing
coverable changed". It is the coverage tool having instrumented nothing. Treated as
valid it produces the worst answer this tool can give: every changed line is a line the
report never mentions, so the denominator is 0 and the gate returns a comfortable 100%
over completely unmeasured code.

Measured before the guard: 50 changed lines against an empty report reported 100% (0/0).

The usual cause on the JVM is a surefire `<argLine>` that overrides instead of appending
`@{argLine}`, which detaches the JaCoCo agent while the build stays green.
"""

from __future__ import annotations

import pytest

from brimyr.cli import main
from brimyr.coverage.model import CoverageReport
from brimyr.gate import EXIT_ERROR
from brimyr.runner import RunOutcome

EMPTY = "<coverage><packages></packages></coverage>"


@pytest.fixture
def empty_report(tmp_path):
    p = tmp_path / "coverage.xml"
    p.write_text(EMPTY)
    return p


def test_run_outcome_is_not_ok_for_an_empty_report():
    """`bool(report)`, not `report is not None`: an empty report is not None."""
    from brimyr.detect import ecosystem

    empty = RunOutcome(
        ecosystem=ecosystem("python"),
        returncode=0,
        coverage_paths=(),
        report=CoverageReport(()),
    )
    assert empty.ok is False  # nosec B101

    # ...but a report that names a file, even an entirely uncovered one, is a real run.
    from brimyr.coverage.model import FileCoverage

    real = RunOutcome(
        ecosystem=ecosystem("python"),
        returncode=0,
        coverage_paths=(),
        report=CoverageReport((FileCoverage("a.py", frozenset(), frozenset({1})),)),
    )
    assert real.ok is True  # nosec B101


def test_coverage_subcommand_errors_rather_than_passing(empty_report, tmp_path):
    code = main(["coverage", "--coverage-file", str(empty_report), "--base", "HEAD~1"])
    assert code == EXIT_ERROR  # nosec B101 - exit 2, never 0


def test_ci_subcommand_errors_rather_than_passing(empty_report):
    code = main(["ci", "--mode", "baseline", "--coverage-file", str(empty_report)])
    assert code == EXIT_ERROR  # nosec B101


def test_a_report_with_files_is_still_fine(tmp_path):
    """The guard must not fire on a real report that happens to be all-uncovered."""
    p = tmp_path / "coverage.xml"
    p.write_text(
        "<coverage><packages><package><classes>"
        '<class filename="a.py"><lines><line number="1" hits="0"/></lines></class>'
        "</classes></package></packages></coverage>"
    )
    code = main(["ci", "--mode", "baseline", "--coverage-file", str(p)])
    assert code == 0  # nosec B101
