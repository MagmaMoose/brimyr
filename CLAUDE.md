# CLAUDE.md

Brimyr is a **patch-coverage gate**. It auto-detects the repo's ecosystem
(Python / JS-TS / .NET), runs the right test command **with coverage on**, and on
a PR gates **only on the coverage of the lines the diff changed** (diff-cover
style, default 80%); pre-existing uncovered code never blocks. Non-blocking, the
same run drives `sonar-scanner` to ship quality + coverage to SonarQube. One
`brimyr` CLI backs three surfaces: `action.yml` (composite action),
`.github/workflows/gate.yml` (reusable workflow), and `.pre-commit-hooks.yaml`
(local pre-push hook).

## Architecture

The design splits a **pure core** from thin **side-effecting edges**:

```
src/brimyr/
  cli.py            # argparse dispatch: coverage | ci | local | version
  coverage/         # ★ THE PURE CORE — deterministic, no I/O, heavily tested
    diff.py         #   unified-diff text -> DiffIndex (changed files + added ranges)
    model.py        #   CoverageReport / FileCoverage + CoverageBuilder (covered-wins)
    lcov.py         #   lcov .info  -> CoverageReport
    cobertura.py    #   Cobertura XML -> CoverageReport
    patch.py        #   DiffIndex ∩ CoverageReport -> PatchCoverage  (the gate's heart)
  git.py            # the ONLY git/subprocess boundary (merge-base, diff, shallow detect)
  detect.py         # ecosystem markers -> Ecosystem (test cmd + coverage format)
  runner.py         # run tests w/ coverage, locate + ingest the file (broken-run rule)
  sonar.py          # sonar-scanner runner (failure-isolated, never raises)
  gate.py           # patch % + threshold -> pass/fail + exit code (broken-run handling)
  modes.py          # PR (gate) vs baseline (no gate)
  report.py         # GitHub job summary + step outputs
  local.py          # local base resolution for the pre-push check
```

`coverage/` is **pure**: it takes parsed data (diff text + a coverage report) and
returns numbers. Do **not** import `subprocess`, `os`, network code, or GitHub
Actions into it — that separation is what makes the crown-jewel `patch.py`
trivially testable and deterministic. `git.py`, `runner.py`, and `sonar.py` are the
only modules that shell out, and each injects its runner so they test without a
real toolchain.

## Two rules that are easy to get wrong

- **Broken test run ≠ 0% coverage.** A failed/empty run is a tool error (exit
  `2`, build red), never a hard 0% gate failure. Lives in `runner.RunResult.broken`
  + `gate.decide_gate(broken=...)`.
- **The denominator is *changed executable* lines.** Blank lines/comments are
  excluded because coverage tools don't report them; files the report never
  mentions contribute nothing (diff-cover behaviour). Nothing coverable changed ⇒
  vacuous 100% pass.

## Conventions

Python ≥ 3.11, **uv + Ruff + pytest**, full type hints, stdlib-only core (lcov by
hand, Cobertura via `xml.etree`). SHA-pin external GitHub Actions with a `# vX.Y.Z`
comment. MIT. Tests mirror modules 1:1 under `tests/`.

**Releases** are automated: pushing to `main` runs Diatreme + python-semantic-release
(single-env TBD, `.github/workflows/release.yaml`), which cuts the next stable
`vX.Y.Z` from conventional commits and bumps `project.version` + `__init__.__version__`
— never bump those by hand.

## Exit-code contract

`0` pass · `1` patch coverage below threshold · `2` broken run / setup / usage error.

## [tooling]

- Prefer targeted line-range reads over whole files.
- grep/find/glob: return matching paths and matched lines only, not whole files.
- After a successful write/edit, trust it; don't re-read just to "verify".
- Full human docs live in `./docs` (MkDocs).
