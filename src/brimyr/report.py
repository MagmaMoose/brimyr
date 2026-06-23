"""Human + machine output helpers: GitHub job summary, step outputs, key=value.

Kept tiny and side-effect-explicit: functions either return strings (pure, easy
to test) or append to the GitHub Actions files named by ``GITHUB_STEP_SUMMARY`` /
``GITHUB_OUTPUT`` when those env vars are present (no-ops otherwise).
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence

from brimyr.detect import Ecosystem
from brimyr.gate import GateDecision
from brimyr.modes import Mode

_MAX_MISSING_FILES = 20


def _fmt_pct(value: float) -> str:
    return f"{value:.1f}%"


def render_summary(
    decision: GateDecision,
    mode: Mode,
    *,
    broken: bool = False,
    ecosystems: Sequence[Ecosystem] = (),
    sonar_message: str | None = None,
) -> str:
    """Render the Markdown job summary for a CI run."""
    patch = decision.patch
    if broken:
        status = "`error`"
    elif decision.failed:
        status = "`fail`"
    else:
        status = "`pass`"

    detected = ", ".join(eco.label for eco in ecosystems) or "—"
    lines: list[str] = ["## 🟣 Brimyr — patch coverage", ""]
    lines.append(f"**Mode:** `{mode.value}` · **Gate:** {status} · **Ecosystem:** {detected}")
    lines.append("")

    if broken:
        lines.append(
            "> ❌ **Broken test run** — the tests failed or produced no coverage. "
            "This is a tool error (build red), **not** 0% patch coverage."
        )
        lines.append("")
        return "\n".join(lines)

    lines.append("| Metric | Value |")
    lines.append("|--------|-------|")
    lines.append(f"| Patch coverage | **{_fmt_pct(patch.percent)}** |")
    lines.append(
        f"| Covered / changed executable lines | {patch.covered_lines} / {patch.total_lines} |"
    )
    if decision.gated:
        lines.append(f"| Threshold | {_fmt_pct(decision.threshold)} |")
    lines.append("")

    if not decision.gated:
        lines.append("📋 Baseline run — coverage shipped to SonarQube; no patch gate.")
        lines.append("")
    elif not patch.has_measurable:
        lines.append("✅ No changed executable lines to cover — vacuous pass.")
        lines.append("")
    elif decision.failed:
        lines.append(
            f"❌ **Patch coverage {_fmt_pct(patch.percent)} is below the "
            f"{_fmt_pct(decision.threshold)} threshold.** Uncovered changed lines:"
        )
        lines.append("")
        for file_result in patch.files[:_MAX_MISSING_FILES]:
            if not file_result.missing_lines:
                continue
            shown = ", ".join(str(n) for n in file_result.missing_lines[:15])
            more = "…" if len(file_result.missing_lines) > 15 else ""
            lines.append(f"- `{file_result.path}` — {shown}{more}")
        if len(patch.files) > _MAX_MISSING_FILES:
            lines.append(f"- … and {len(patch.files) - _MAX_MISSING_FILES} more file(s)")
        lines.append("")
    else:
        lines.append(
            f"✅ Patch coverage {_fmt_pct(patch.percent)} meets the "
            f"{_fmt_pct(decision.threshold)} threshold."
        )
        lines.append("")

    if sonar_message:
        lines.append(f"**SonarQube:** {sonar_message}")
        lines.append("")

    return "\n".join(lines)


def append_step_summary(text: str) -> None:
    """Append Markdown to the GitHub job summary, if running under Actions."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")


def write_outputs(pairs: Mapping[str, str]) -> None:
    """Append ``key=value`` action outputs, if running under Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as handle:
        for key, value in pairs.items():
            handle.write(f"{key}={value}\n")
