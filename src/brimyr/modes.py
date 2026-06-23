"""Run-mode resolution: PR (patch-coverage gate) vs baseline (trend only).

* **PR events** → ``Mode.PR``: run tests with coverage, gate on patch coverage,
  ship the report to SonarQube.
* **Push to default branch / scheduled** → ``Mode.BASELINE``: run tests with
  coverage and ship to SonarQube for the trend — but **no** patch gate (there is
  no PR diff to measure against).
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
        """Whether this mode applies the patch-coverage gate."""
        return self is Mode.PR


def resolve_mode(explicit: str | None = None, event_name: str | None = None) -> Mode:
    """Resolve the run mode from an explicit flag (``auto`` defers to the event)."""
    if explicit and explicit.lower() not in ("", "auto"):
        return Mode(explicit.lower())
    if event_name and event_name.lower() in _PR_EVENTS:
        return Mode.PR
    return Mode.BASELINE
