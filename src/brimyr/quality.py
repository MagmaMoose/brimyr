"""Gate on net-new **quality** findings that Chargate classified — pure.

Brimyr is quality assurance; Chargate is security assurance. Chargate already owns
a finished net-new engine (``chargate filter-sarif``), so brimyr does not import it,
vendor it, or re-implement it — it *calls* it across a process boundary and gates on
what comes back. See ``.claude/decisions/0002-quality-gate-calls-chargate.md``.

Two files come back, and this module reads exactly one of them for the verdict:

**The counts JSON is the gate's only input.** ``per_level_net_new`` is chargate's own
tally of net-new results by SARIF level, and every net-new result contributes to it —
so ``sum(per_level_net_new.values())`` must equal ``net_new_count``. It doesn't just
happen to; it is checked (:func:`parse_counts`), because a counts file that disagrees
with itself is the shape of every silent pass this codebase has ever shipped.

**The filtered SARIF is display-only.** :func:`read_finding_lines` skims it for
``path:line [rule]`` strings to list in the summary. It builds no SARIF model, resolves
no severity, and never touches the verdict — that is chargate's job, and duplicating it
here would be the coupling the process boundary exists to avoid. Its one hard rule is
that its result *count* must match ``net_new_count`` (:func:`check_findings_consistent`);
a dropped or stale report otherwise reads as a comfortable "0 findings" pass.

**Severity is the SARIF level, not the security-severity band.** Chargate's own gate
works either way: it reads *per-result* verdicts, so ``effective_band`` uses a numeric
``security-severity`` when the tool emits one and otherwise derives a band from the level
(error→high, warning→medium, note→low). Brimyr has no verdicts — only the counts
document — and there the two breakdowns are *not* interchangeable: ``per_severity_*`` is
populated **only** from a real ``security-severity`` property, which quality linters
essentially never emit, while ``per_level_*`` covers every result. A band-valued
threshold read off this document would therefore match nothing, on every PR, forever,
while looking exactly like a configured gate. So ``fail_on`` speaks SARIF levels, every
one of which is reachable. Chargate's ``fail-on: high`` is this module's
``fail_on: error``.

**Report-only by default.** ``fail_on`` defaults to ``"none"``: MegaLinter's quality half
over a mature repo is a far denser event than its security half, and the first real PR
going red with hundreds of findings is how a gate becomes decoration nobody reads. Ship
it reporting, measure a release cycle, then pick a level.

**A scan that did not finish is an error, not zero findings.** ``chargate ci`` writes its
counts JSON *before* it decides whether the scan produced anything, so a run that scanned
nothing leaves a well-formed document full of zeros on disk and only then exits 2. Read
on its own that file is indistinguishable from a clean pull request. So the caller tells
this module the scan broke (:func:`broken_decision`) and no counts are read at all —
mirroring the coverage gate, where a failed test run is a tool error rather than 0%.

Exit-code contract, matching the coverage gate: ``0`` pass · ``1`` blocking net-new
findings · ``2`` broken scan / unreadable / self-inconsistent / unrecognised input.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Versions of chargate's ``--counts-json`` schema this build knows how to read.
#: A value outside this set is a hard error (exit 2), never a pass: the two sides of
#: the process boundary release independently, and a gate that cannot evaluate its
#: input must not report success. This is the same rule chargate already applies to an
#: empty SARIF.
SUPPORTED_COUNTS_SCHEMA_VERSIONS = frozenset({1})

#: ``fail_on`` values, in the SARIF level vocabulary chargate's counts are keyed by.
#: ``none`` is report-only; ``any`` blocks on every net-new finding including ones the
#: producing linter left unlevelled.
FAIL_ON_CHOICES = ("none", "note", "warning", "error", "any")

DEFAULT_FAIL_ON = "none"

# SARIF levels, ranked. `error` is the top because SARIF has no level above it — a
# `critical` here would be a threshold nothing could ever reach, which is precisely the
# silent-never-blocks hole this module refuses to have.
_LEVEL_RANK = {"none": 0, "note": 1, "warning": 2, "error": 3}

#: How chargate's severity bands line up with the SARIF levels this gate speaks, for
#: anyone translating a `chargate fail_on` value. Documentation, not logic.
BAND_EQUIVALENT = {"error": "high", "warning": "medium", "note": "low"}

# Cap the listing so a first-run PR with 400 findings produces a readable summary
# rather than a wall. The count is always stated in full; only the listing truncates.
_MAX_LISTED = 20


class QualityInputError(ValueError):
    """A quality input could not be read, or contradicts itself. Always exit 2."""


@dataclass(frozen=True)
class QualityCounts:
    """Chargate's net-new tally for one quality scan, as read from the counts JSON."""

    net_new: int
    total: int
    pre_existing: int
    suppressed: int = 0
    per_level_net_new: dict[str, int] = field(default_factory=dict)
    per_level_total: dict[str, int] = field(default_factory=dict)
    schema_version: int = 1

    def net_new_at_or_above(self, level: str) -> int:
        """Net-new findings whose level ranks at or above ``level``."""
        floor = _LEVEL_RANK[level]
        return sum(
            count
            for name, count in self.per_level_net_new.items()
            if _LEVEL_RANK.get(name, 0) >= floor
        )


@dataclass(frozen=True)
class QualityDecision:
    """The verdict for one quality run."""

    counts: QualityCounts
    fail_on: str
    failed: bool
    blocking: int
    # Whether this run could block at all. False for two different reasons — a
    # `fail_on: none` threshold, or a run with no diff to gate (baseline) — and the
    # summary has to name the right one, so `reason` carries which.
    gated: bool
    reason: str = ""
    # The scan never completed, so `counts` is a placeholder and means nothing. Exit 2,
    # exactly as a failed test run is an error rather than 0% coverage.
    broken: bool = False
    # Linters the scan could not run, verbatim from chargate. A *completed* scan is not
    # necessarily a *full* one — chargate exits 0 having declined to start a linter it
    # has no image for — and a smaller scan reporting nothing looks exactly like a clean
    # repo. Never gates; it is said out loud instead, which is the contract chargate
    # already applies to its own degraded runs.
    scan_note: str = ""
    # `path:line [rule]` strings skimmed from the filtered SARIF for the summary, or
    # empty when no `--findings` file was supplied. Never feeds `failed`.
    listing: tuple[str, ...] = ()
    listing_truncated: int = 0

    @property
    def exit_code(self) -> int:
        if self.broken:
            return 2
        return 1 if self.failed else 0


def _as_mapping(payload: Any, what: str) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise QualityInputError(f"{what} is not a JSON object")
    return payload


def _as_int(payload: dict[str, Any], key: str, what: str) -> int:
    value = payload.get(key)
    # bool is an int subclass and `True` would silently read as 1.
    if isinstance(value, bool) or not isinstance(value, int):
        raise QualityInputError(f"{what} has no integer {key!r}")
    return value


def _as_level_map(payload: dict[str, Any], key: str, what: str) -> dict[str, int]:
    value = payload.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise QualityInputError(f"{what} has a non-object {key!r}")
    out: dict[str, int] = {}
    for name, count in value.items():
        if isinstance(count, bool) or not isinstance(count, int):
            raise QualityInputError(f"{what} has a non-integer count for {key}[{name!r}]")
        out[str(name)] = count
    return out


def _optional_int(payload: dict[str, Any], key: str, fallback: int) -> int:
    """An integer the gate only *displays*: absent or malformed falls back, never raises."""
    value = payload.get(key)
    return fallback if isinstance(value, bool) or not isinstance(value, int) else value


def parse_counts(payload: Any) -> QualityCounts:
    """Validate chargate's counts JSON and read the fields this gate needs.

    Raises :class:`QualityInputError` — i.e. exit 2, never a pass — when the document
    is not an object, carries a ``schema_version`` this build does not recognise, omits
    a field the gate reads, or disagrees with itself about how many net-new findings
    there are.

    A *missing* ``schema_version`` is an old chargate, which is a version-skew error in
    exactly the same way an unknown one is: the file predates the contract that makes
    reading it safe.
    """
    document = _as_mapping(payload, "counts JSON")
    version = document.get("schema_version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise QualityInputError(
            "counts JSON has no integer 'schema_version' — it was written by a chargate "
            "older than the documented filter-sarif contract. Bump the pinned "
            "magmamoose/chargate ref."
        )
    if version not in SUPPORTED_COUNTS_SCHEMA_VERSIONS:
        known = ", ".join(str(v) for v in sorted(SUPPORTED_COUNTS_SCHEMA_VERSIONS))
        raise QualityInputError(
            f"counts JSON schema_version {version} is not one this brimyr understands "
            f"({known}). Refusing to gate on a document it cannot read — upgrade brimyr "
            "or pin an older magmamoose/chargate."
        )

    net_new = _as_int(document, "net_new_count", "counts JSON")
    total = _as_int(document, "total_count", "counts JSON")
    per_level_net_new = _as_level_map(document, "per_level_net_new", "counts JSON")
    per_level_total = _as_level_map(document, "per_level_total", "counts JSON")

    # Every net-new result contributes exactly one level to this map (chargate's
    # `count_results`), so the two numbers are the same number counted two ways. When
    # they disagree, one of them is wrong and the gate has no way to tell which —
    # and the wrong one is always the one that makes the PR pass.
    levelled = sum(per_level_net_new.values())
    if levelled != net_new:
        raise QualityInputError(
            f"counts JSON disagrees with itself: net_new_count={net_new} but "
            f"per_level_net_new sums to {levelled}"
        )

    # Neither is read by the gate, so an absent one falls back rather than raising —
    # but a `true` must not read as 1, hence the bool guard on both.
    return QualityCounts(
        net_new=net_new,
        total=total,
        pre_existing=_optional_int(document, "pre_existing_count", total - net_new),
        suppressed=_optional_int(document, "suppressed_count", 0),
        per_level_net_new=per_level_net_new,
        per_level_total=per_level_total,
        schema_version=version,
    )


def count_sarif_results(payload: Any) -> int:
    """How many results the filtered SARIF carries. Shape only — no SARIF model.

    Deliberately shallow: this walks ``runs[].results[]`` and counts. It resolves no
    level, no severity and no rule metadata, because classifying a SARIF result is
    chargate's job and a second implementation of it here is the coupling the process
    boundary exists to avoid.
    """
    document = _as_mapping(payload, "findings SARIF")
    runs = document.get("runs")
    if runs is None:
        raise QualityInputError("findings SARIF has no 'runs' array")
    if not isinstance(runs, list):
        raise QualityInputError("findings SARIF has a non-array 'runs'")
    total = 0
    for run in runs:
        if not isinstance(run, dict):
            raise QualityInputError("findings SARIF has a non-object entry in 'runs'")
        results = run.get("results") or []
        if not isinstance(results, list):
            raise QualityInputError("findings SARIF has a non-array 'results'")
        total += len(results)
    return total


def read_finding_lines(payload: Any) -> tuple[str, ...]:
    """Skim the filtered SARIF for ``path:line [rule]`` strings, for the summary.

    Display-only, and shallow on purpose (see :func:`count_sarif_results`). Anything it
    cannot read degrades to a less specific string rather than raising: this text is
    decoration on a verdict already decided by :func:`parse_counts`.
    """
    document = _as_mapping(payload, "findings SARIF")
    lines: list[str] = []
    for run in document.get("runs") or []:
        if not isinstance(run, dict):
            continue
        for result in run.get("results") or []:
            if not isinstance(result, dict):
                continue
            lines.append(_describe(result))
    return tuple(lines)


def _describe(result: dict[str, Any]) -> str:
    uri, line = _primary_location(result)
    where = uri or "(no location)"
    if line is not None:
        where = f"{where}:{line}"
    rule = result.get("ruleId")
    return f"{where} [{rule}]" if isinstance(rule, str) and rule else where


def _primary_location(result: dict[str, Any]) -> tuple[str | None, int | None]:
    locations = result.get("locations")
    if not isinstance(locations, list) or not locations:
        return None, None
    first = locations[0]
    if not isinstance(first, dict):
        return None, None
    physical = first.get("physicalLocation")
    if not isinstance(physical, dict):
        return None, None
    artifact = physical.get("artifactLocation")
    uri = artifact.get("uri") if isinstance(artifact, dict) else None
    region = physical.get("region")
    start = region.get("startLine") if isinstance(region, dict) else None
    return (
        uri if isinstance(uri, str) else None,
        start if isinstance(start, int) and not isinstance(start, bool) else None,
    )


def check_findings_consistent(counts: QualityCounts, sarif_results: int) -> None:
    """Assert the filtered SARIF holds exactly the findings the counts claim.

    Chargate writes both files from one in-memory result, so a mismatch means one of
    them is stale, truncated or from another run. Raising here (exit 2) rather than
    trusting either is the same rule as everywhere else in this codebase: a dropped
    report reads as a comfortable pass, so it must read as an error instead.
    """
    if sarif_results != counts.net_new:
        raise QualityInputError(
            f"findings SARIF holds {sarif_results} result(s) but the counts JSON claims "
            f"{counts.net_new} net-new — one of the two is stale or truncated"
        )


def broken_decision(fail_on: str = DEFAULT_FAIL_ON) -> QualityDecision:
    """The verdict for a quality scan that never completed. Always exit 2.

    Deliberately reads no files. The counts document chargate leaves behind after a scan
    that found no runs is a well-formed set of zeros, and parsing it would turn a scan
    that did not happen into a clean bill of health — the silent pass this codebase
    treats as the default failure mode rather than an edge case.
    """
    return QualityDecision(
        counts=QualityCounts(net_new=0, total=0, pre_existing=0),
        fail_on=fail_on.strip().lower(),
        failed=False,
        blocking=0,
        gated=False,
        reason="broken",
        broken=True,
    )


def decide_quality_gate(
    counts: QualityCounts,
    fail_on: str = DEFAULT_FAIL_ON,
    *,
    gate: bool = True,
    listing: tuple[str, ...] = (),
    scan_note: str = "",
) -> QualityDecision:
    """Decide whether the net-new quality findings block, at ``fail_on``.

    ``gate=False`` makes the run report-only regardless of ``fail_on`` (baseline mode,
    which has no diff and therefore no net-new set worth gating). ``listing`` is the
    display-only text from :func:`read_finding_lines`, and ``scan_note`` names any
    linters the scan could not run.
    """
    normalized = fail_on.strip().lower()
    if normalized not in FAIL_ON_CHOICES:
        raise QualityInputError(
            f"invalid fail_on {fail_on!r}; choose one of {', '.join(FAIL_ON_CHOICES)}"
        )

    if normalized == "none":
        blocking = 0
    elif normalized == "any":
        blocking = counts.net_new
    else:
        blocking = counts.net_new_at_or_above(normalized)

    shown = listing[:_MAX_LISTED]
    if not gate:
        reason = "baseline"
    elif normalized == "none":
        reason = "threshold"
    else:
        reason = ""
    return QualityDecision(
        counts=counts,
        fail_on=normalized,
        failed=bool(gate and blocking),
        blocking=blocking,
        gated=gate and normalized != "none",
        reason=reason,
        listing=shown,
        listing_truncated=max(0, len(listing) - len(shown)),
        scan_note=scan_note.strip(),
    )
