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

from brimyr.coverage.patch import PatchCoverage, TotalCoverage

EXIT_OK = 0
EXIT_BLOCKED = 1
EXIT_ERROR = 2

DEFAULT_THRESHOLD = 80.0

# Below this many changed executable lines the percentage is too coarse to gate on: one
# uncovered line out of three is 67%, which fails an 80% threshold while telling you
# nothing. SonarQube uses exactly this rule and exactly this number — "the conditions on
# coverage are ignored until the number of new lines to cover is at least 20" — so
# matching it keeps a Brimyr verdict and a Sonar verdict from disagreeing on small PRs.
#
# It IS a hole: a 19-line untested change passes. That is the same shape of hole as the
# one that makes Sonar's project-level gate unusable, just much smaller, and it is why
# Brimyr SAYS SO in the summary rather than passing quietly. Set to 0 to gate everything.
DEFAULT_MIN_LINES = 20


@dataclass(frozen=True)
class GateDecision:
    """The verdict for one patch-coverage run."""

    patch: PatchCoverage
    threshold: float
    failed: bool
    broken: bool
    gated: bool
    # Reported, never gated on — the chargate split, where the build blocks on net-new
    # findings but the full picture still ships. Trailing and optional so every existing
    # construction stays valid; `failed` and `exit_code` deliberately ignore it.
    total: TotalCoverage | None = None
    # The sample-size floor in force, and whether this run fell under it. Kept on the
    # decision so the summary can SAY the gate was skipped — a silent exemption is how a
    # coverage gate quietly stops meaning anything.
    min_lines: int = DEFAULT_MIN_LINES
    below_min_lines: bool = False

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
    total: TotalCoverage | None = None,
    min_lines: int = DEFAULT_MIN_LINES,
) -> GateDecision:
    """Decide whether patch coverage blocks, given a threshold.

    ``broken`` forces an error verdict (a failed/empty test run). ``gate=False``
    makes the run report-only (baseline mode) — coverage is computed and shipped,
    nothing blocks. ``min_lines`` is the sample size below which the percentage is too
    coarse to be worth gating on (see :data:`DEFAULT_MIN_LINES`); 0 gates everything.
    """
    if not (0.0 <= threshold <= 100.0):
        raise ValueError(f"threshold must be between 0 and 100, got {threshold}")
    if min_lines < 0:
        raise ValueError(f"min_lines must be >= 0, got {min_lines}")

    too_small = 0 < patch.total_lines < min_lines
    if broken or not gate:
        failed = False
    elif not patch.has_measurable:
        failed = False  # nothing coverable changed → vacuous pass
    elif too_small:
        failed = False  # sample too small for the percentage to mean anything
    else:
        failed = patch.percent < threshold

    return GateDecision(
        patch=patch,
        threshold=threshold,
        failed=failed,
        broken=broken,
        gated=gate,
        total=total,
        min_lines=min_lines,
        below_min_lines=too_small,
    )
