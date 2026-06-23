"""Turn a patch-coverage result into a pass/fail gate decision.

The gate blocks when patch coverage — the % of *changed executable lines* the
tests covered — is below a threshold (default **80%**). Two cases never hard-fail
the gate:

* **Broken run.** A failed/empty test run is a tool error (exit ``2``, build red),
  *not* "0% patch coverage". Reporting a broken run as a 0% gate failure would be
  actively misleading; this mirrors the pipeline's rule that a broken scanner is a
  tool error, not a finding.
* **Nothing coverable changed.** A docs/config-only PR has an empty denominator
  and passes vacuously (100%).

Exit-code contract: ``0`` pass · ``1`` patch coverage below threshold · ``2``
broken run / setup error (the CLI maps usage errors here too).
"""

from __future__ import annotations

from dataclasses import dataclass

from brimyr.coverage.patch import PatchCoverage

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2

DEFAULT_THRESHOLD = 80.0


@dataclass(frozen=True)
class GateDecision:
    """The verdict for one patch-coverage run."""

    patch: PatchCoverage
    threshold: float
    failed: bool
    broken: bool
    gated: bool

    @property
    def percent(self) -> float:
        return self.patch.percent

    @property
    def exit_code(self) -> int:
        if self.broken:
            return EXIT_ERROR
        return EXIT_BLOCKED if self.failed else EXIT_OK


def decide_gate(
    patch: PatchCoverage,
    threshold: float = DEFAULT_THRESHOLD,
    *,
    broken: bool = False,
    gate: bool = True,
) -> GateDecision:
    """Decide whether patch coverage blocks, given a threshold.

    ``broken`` forces an error verdict (a failed/empty test run). ``gate=False``
    makes the run report-only (baseline mode) — coverage is computed and shipped,
    nothing blocks.
    """
    if not (0.0 <= threshold <= 100.0):
        raise ValueError(f"threshold must be between 0 and 100, got {threshold}")

    if broken or not gate:
        failed = False
    elif not patch.has_measurable:
        failed = False  # nothing coverable changed → vacuous pass
    else:
        failed = patch.percent < threshold

    return GateDecision(
        patch=patch,
        threshold=threshold,
        failed=failed,
        broken=broken,
        gated=gate,
    )
