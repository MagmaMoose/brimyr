"""Run-mode resolution: PR (gate) vs baseline (trend only).

* **PR events** → ``Mode.PR``: run tests with coverage, gate on patch coverage,
  ship the report to SonarQube. When the quality half is on, it gates too.
* **Push to default branch / scheduled** → ``Mode.BASELINE``: run tests with
  coverage and ship to SonarQube for the trend — but **no** gate on either half
  (there is no PR diff to measure against, so neither patch coverage nor the
  net-new finding set means anything).
"""

from __future__ import annotations

from enum import StrEnum

# Both PR events gate, but wire Brimyr on ``pull_request``, not
# ``pull_request_target``: Brimyr runs the PR's *own* test code, and
# ``pull_request_target`` runs it with the base repo's write token + secrets — a
# foothold for a malicious fork. ``pull_request_target`` is recognised here only so
# an existing setup still gates. See the security note in docs/setup.md.
_PR_EVENTS = {"pull_request", "pull_request_target"}


class Mode(StrEnum):
    PR = "pr"
    BASELINE = "baseline"

    @property
    def gates(self) -> bool:
        """Whether this mode gates at all.

        Governs **both** halves: ``cli`` passes it to the patch-coverage gate and,
        via ``decide_quality_gate(gate=...)``, to the net-new quality half. Baseline
        has no diff, so a quality run there is report-only for a different reason than
        a ``fail_on: none`` one — which is why the summary names which.
        """
        return self is Mode.PR


def resolve_mode(explicit: str | None = None, event_name: str | None = None) -> Mode:
    """Resolve the run mode from an explicit flag (``auto`` defers to the event)."""
    if explicit and explicit.lower() not in ("", "auto"):
        return Mode(explicit.lower())
    if event_name and event_name.lower() in _PR_EVENTS:
        return Mode.PR
    return Mode.BASELINE
