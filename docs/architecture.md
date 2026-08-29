# Architecture

<!-- sources: src/brimyr/cli.py, src/brimyr/coverage/patch.py, src/brimyr/quality.py,
     src/brimyr/runner.py, broker/app/broker.py
     -->

Brimyr is one `brimyr` Python CLI (`src/brimyr/cli.py:main`) behind three GitHub
surfaces, running two gates: **patch coverage** and **net-new quality findings**. The
design splits cleanly into a **pure core** and a thin set of **side-effecting edges**.

## Module map

```text
src/brimyr/
  cli.py          # argparse dispatch: coverage | ci | local | lint | version
  coverage/       # ★ THE PURE CORE: deterministic, no I/O, heavily tested
    diff.py       #   unified-diff text -> DiffIndex (changed files + added line ranges)
    model.py      #   CoverageReport / FileCoverage + CoverageBuilder (covered-wins merge)
    lcov.py       #   lcov .info    -> CoverageReport
    cobertura.py  #   Cobertura XML -> CoverageReport
    jacoco.py     #   JaCoCo XML    -> CoverageReport (its own format, not Cobertura)
    patch.py      #   DiffIndex ∩ CoverageReport -> PatchCoverage  (the gate's heart)
                  #   + compute_total_coverage: the reported, never-gated total
  git.py          # the ONLY git/subprocess boundary (merge-base, diff, shallow detect)
  detect.py       # ecosystem markers -> Ecosystem (test command + coverage format)
  runner.py       # run tests with coverage, locate + ingest the file (broken-run rule)
  sonar.py        # sonar-scanner runner (failure-isolated, never raises)
  sonar_dotnet.py # dotnet sonarscanner begin/end WRAPPING the build (.NET only)
  html_report.py  # ReportGenerator wrapper -> browsable HTML artifact (optional)
  gate.py         # patch % + threshold -> pass/fail + exit code
  quality.py      # pure: chargate's net-new counts + fail_on level -> a verdict
  modes.py        # PR (gate) vs baseline (no gate) resolution
  report.py       # GitHub job summary + step outputs (also renders the PR comment)
  github_comment.py # marker-based upsert of the ONE PR comment (never raises)
  broker_client.py  # Actions OIDC -> Brimyr[bot] token (fails soft, falls back)
  local.py        # local base resolution for the pre-push check
```

## The design rule

`coverage/` is **pure**: it takes already-parsed data (unified-diff text + a
coverage report) and returns numbers. `git.py`, `runner.py`, `sonar.py`,
`sonar_dotnet.py` and `html_report.py` are the only modules that shell out, and each
injects its runner, so the core is unit-tested with synthetic diff text and coverage
strings, no real repository or toolchain required. `quality.py` is pure in the same
way without living under `coverage/`: it is handed already-parsed JSON and returns a
verdict, and `cli.py` does the reading. The two network edges, `github_comment.py` and
`broker_client.py`, follow the same shape: stdlib `urllib` only, with the opener
injected so the tests need no network, and neither ever raises out into the gate.

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
8. **`quality.decide_quality_gate`** (optional) decides the net-new half on `fail_on`,
   over the counts `cli` read and `quality.parse_counts` validated out of the JSON the
   nested Chargate step left behind, or, told the scan never completed,
   `quality.broken_decision` reads nothing and reports a tool error. Same `Mode.gates`
   flag as coverage, so baseline gates neither.
9. **`report`** writes the GitHub job summary and step outputs, `render_summary`'s
   coverage block, with `render_quality_summary`'s net-new block appended when that
   half ran.
10. **`github_comment.post_pr_comment`** (optional) puts that same rendered summary
    on the PR as a single marker-owned comment, creating it once, then `PATCH`ing it
    on every later push. When `token_broker_url` is set,
    **`broker_client.mint_bot_token`** first exchanges the job's Actions OIDC token
    for a `Brimyr[bot]` installation token; on any failure it returns `None` and the
    job's `GITHUB_TOKEN` is used instead. Both are failure-isolated, like Sonar.

Baseline mode skips the gating, both halves. It computes coverage against an empty
`DiffIndex`, ships to Sonar, and never blocks; a baseline quality run is report-only
for its own reason (no diff, so no net-new set worth gating), which the summary names
rather than blaming the threshold.

The process exits with the worse of the two halves' codes.

## The quality half calls Chargate instead of importing it

Brimyr is quality assurance, and coverage is only half of that. The other half judges
the **net-new** findings a MegaLinter quality run produced. Chargate already owns a
finished net-new engine (`chargate filter-sarif`), so brimyr does not vendor it or
re-implement it: `action.yml` runs `magmamoose/chargate` as a nested step, and
`quality.py` reads the two files that step leaves behind. The `--counts-json` document
is the verdict's only input; the filtered SARIF is display-only, skimmed for the
`path:line [rule]` strings the summary lists. Anything unreadable, unrecognised, or
self-contradicting is exit `2`, because a half that cannot evaluate must not report a
pass, and so is a scan that never completed, which `action.yml` detects from the nested
step's `outcome` and passes on as `--quality-scan-broken`, reading no file at all.

`brimyr lint` runs that half on its own, under its own PR-comment marker.
`brimyr ci --quality-counts` instead folds the same verdict into the one job summary
and the one PR comment coverage already writes, which is the consolidated view and the
reason to prefer it. What the threshold means, and how to turn the half on, are on
[Quality findings](quality-findings.md).

## The token broker is not part of the CLI

`broker/` in the repository is a **separate deployable**: the AWS Lambda service
that mints `Brimyr[bot]` tokens, with its own `pyproject.toml`, lockfile, Ruff
config and CI job. Nothing under `src/brimyr` imports it; `broker_client.py` talks
to it over HTTPS like any other remote service, which is what keeps the CLI
stdlib-only and dependency-free. Its architecture, local LocalStack loop and go-live
runbook live with the code, in
[`broker/README.md`](https://github.com/MagmaMoose/brimyr/blob/main/broker/README.md).

## Exit-code contract

| Code | Meaning |
| --- | --- |
| `0` | pass |
| `1` | patch coverage below threshold, or blocking net-new quality findings |
| `2` | broken test run / setup / usage error, or unreadable quality input |

A *broken* test run is a tool error (`2`), never "0% patch coverage". When both halves
run in one `brimyr ci`, the process exits with the worse of the two codes on that same
`0 < 1 < 2` scale: clean coverage never launders a blocking quality finding.

## Testing

Tests mirror modules 1:1 under `tests/` (e.g. `test_patch.py`, `test_gate.py`,
`test_cobertura.py`). The pure core is tested with synthetic inputs; the
subprocess boundaries inject their runner so they are exercised without a real
toolchain, git, or a live SonarQube.
