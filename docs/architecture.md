# Architecture

Brimyr is one `brimyr` Python CLI (`src/brimyr/cli.py:main`) behind three GitHub
surfaces. The design splits cleanly into a **pure core** and a thin set of
**side-effecting edges**.

## Module map

```
src/brimyr/
  cli.py          # argparse dispatch: coverage | ci | local | version
  coverage/       # ★ THE PURE CORE — deterministic, no I/O, heavily tested
    diff.py       #   unified-diff text -> DiffIndex (changed files + added line ranges)
    model.py      #   CoverageReport / FileCoverage + CoverageBuilder (covered-wins merge)
    lcov.py       #   lcov .info   -> CoverageReport
    cobertura.py  #   Cobertura XML -> CoverageReport
    patch.py      #   DiffIndex ∩ CoverageReport -> PatchCoverage  (the gate's heart)
  git.py          # the ONLY git/subprocess boundary (merge-base, diff, shallow detect)
  detect.py       # ecosystem markers -> Ecosystem (test command + coverage format)
  runner.py       # run tests with coverage, locate + ingest the file (broken-run rule)
  sonar.py        # sonar-scanner runner (failure-isolated, never raises)
  gate.py         # patch % + threshold -> pass/fail + exit code
  modes.py        # PR (gate) vs baseline (no gate) resolution
  report.py       # GitHub job summary + step outputs
  local.py        # local base resolution for the pre-push check
```

## The design rule

`coverage/` is **pure**: it takes already-parsed data (unified-diff text + a
coverage report) and returns numbers. `git.py`, `runner.py`, and `sonar.py` are the
only modules that shell out, and each injects its runner, so the core is unit-tested
with synthetic diff text and coverage strings — no real repository or toolchain
required.

!!! warning "Keep the boundary"
    Do **not** import `subprocess`, `os`, network code, or GitHub Actions into
    `coverage/`. That separation is what makes the crown-jewel `patch.py` trivially
    testable and deterministic.

## Data flow (PR / gate mode)

1. **`modes.resolve_mode`** decides PR (gate) vs baseline (no gate) from
   `GITHUB_EVENT_NAME` or an explicit flag.
2. **`detect.detect_ecosystems`** sniffs marker files → the ecosystem(s) and their
   test commands (or the escape hatch / forced ecosystem is used instead).
3. **`runner.run_tests`** runs each ecosystem's command with coverage on, locates
   the emitted file, and parses it (`coverage.lcov` / `coverage.cobertura`) into a
   `CoverageReport`. A failed/empty run sets `RunResult.broken`.
4. **`git.compute_changed_lines`** resolves `merge-base(base, head)`, runs
   `git diff --unified=0`, and hands the text to `coverage.diff.parse_unified_diff`
   → a `DiffIndex`.
5. **`coverage.patch.compute_patch_coverage`** intersects the diff with the report
   → a `PatchCoverage` (covered / total changed-executable lines, per-file misses).
6. **`gate.decide_gate`** applies the threshold (and the broken-run rule) → a
   `GateDecision` and exit code.
7. **`sonar.run_scanner`** (optional) ships quality + coverage to SonarQube. It is
   failure-isolated: it never raises, so a Sonar outage can't fail the gate.
8. **`report`** writes the GitHub job summary and step outputs.

Baseline mode skips the gating: it computes coverage against an empty `DiffIndex`,
ships to Sonar, and never blocks.

## Exit-code contract

| Code | Meaning |
| --- | --- |
| `0` | pass |
| `1` | patch coverage below threshold |
| `2` | broken test run / setup / usage error |

A *broken* test run is a tool error (`2`), never "0% patch coverage".

## Testing

Tests mirror modules 1:1 under `tests/` (e.g. `test_patch.py`, `test_gate.py`,
`test_cobertura.py`). The pure core is tested with synthetic inputs; the
subprocess boundaries inject their runner so they are exercised without a real
toolchain, git, or a live SonarQube.
