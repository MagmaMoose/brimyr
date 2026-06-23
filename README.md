# Brimyr

[![CI](https://github.com/magmamoose/brimyr/actions/workflows/ci.yml/badge.svg)](https://github.com/magmamoose/brimyr/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/magmamoose/brimyr)](LICENSE)

Brimyr is a **patch-coverage gate**. On a pull request it auto-detects the repo's
ecosystem, runs the right test command with coverage instrumentation on, and gates
**only on the coverage of the lines the PR changed** (diff-cover style) — blocking
below a threshold (default **80%**). Pre-existing uncovered code never blocks. The
same run, non-blocking, drives `sonar-scanner` to ship quality + coverage to
SonarQube for the trend.

It is the coverage sibling of [Chargate](https://github.com/MagmaMoose/chargate)
(net-new security/lint gate) and [Diatreme](https://github.com/MagmaMoose/diatreme)
(build + release), and like Diatreme it **auto-detects the ecosystem** and runs the
right command for it.

## Two faces, kept separate

| Face | Blocking? | What it is |
| --- | --- | --- |
| **Patch-coverage gate** | **yes** | The % of *changed executable lines* covered, diffed against the merge-base. Blocks below the threshold. Computed **locally** — no SonarQube involvement. |
| **`sonar-scanner` run** | no | One Sonar run performs Sonar's native quality analysis **and** ingests the coverage file → SonarQube, for the coverage/quality trend. |

Sonar derives new-vs-old code itself (its **New Code Period**); you never feed it
"net-new" coverage. Net-new is the gate's job, and the gate doesn't need Sonar.

## Why patch coverage?

Requiring 80% coverage on a whole legacy codebase is a non-starter; ignoring
coverage on new code lets it rot. Patch coverage splits the difference: hold *new
and changed* lines to a bar, leave the back-catalogue alone.

- **Gate** on what *this PR* changed → actionable, no legacy-debt noise.
- **Ship** the full coverage to SonarQube → the long-run trend and quality gate.

## Coverage is a byproduct of the test run

You run the tests **with instrumentation on** (`pytest --cov`, `jest --coverage`,
`dotnet test --collect`) and that single run emits the coverage file. There is no
separate "measure coverage" pass. Brimyr auto-detects which command to run:

| Ecosystem | Markers | Test command | Coverage |
| --- | --- | --- | --- |
| **Python** | `pyproject.toml`, `setup.py`, `tox.ini`, … | `pytest --cov --cov-report=xml` | Cobertura |
| **JS / TS** | `package.json` | `jest --coverage --coverageReporters=lcov` | lcov |
| **.NET** | `*.csproj`, `*.sln`, … | `dotnet test --collect:"XPlat Code Coverage"` | Cobertura |

Polyglot repos (a JS frontend + a Python backend) match more than one and produce
**one coverage file per language**; Brimyr runs each and merges the reports.

> ⚠️ **Broken test run ≠ 0% coverage.** If the test run fails, times out, or
> produces no coverage, that is a **tool error (build red)** — never reported as
> "0% patch coverage" that hard-fails the gate.

## Three surfaces

| Surface | What it is | When to use |
| --- | --- | --- |
| **Reusable workflow** | `.github/workflows/gate.yml` (`on: workflow_call`) | Easiest — a consumer's whole config is ~one job block. |
| **Composite action** | `action.yml` | When you compose your own steps (e.g. custom toolchain setup). |
| **pre-push hook** | `.pre-commit-hooks.yaml` (`brimyr` hook) | Catch a coverage shortfall locally before pushing. |

All three drive the same `brimyr` Python CLI.

### 1. Reusable workflow (recommended)

```yaml
# .github/workflows/coverage.yml
name: Coverage
on:
  pull_request:
  push:
    branches: [main]

jobs:
  brimyr:
    uses: magmamoose/brimyr/.github/workflows/gate.yml@v1
    with:
      setup: pip install -e '.[test]'        # install your test deps first
    secrets:
      sonar_token: ${{ secrets.SONAR_TOKEN }} # optional
```

On PRs it runs your tests with coverage, gates on patch coverage, and (if
`sonar_url` is set) ships to SonarQube. On push to the default branch it runs a
non-gating baseline that still feeds the trend. Brimyr runs the tests **on the
runner**, so install the toolchain/deps in `setup` (or feed a ready-made report
via `coverage_file`).

### 2. Composite action

```yaml
name: Coverage
on: [pull_request]

permissions:
  contents: read
  pull-requests: read

jobs:
  brimyr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v6
        with: { python-version: '3.12' }
      - run: pip install -e '.[test]'          # your test deps
      - uses: magmamoose/brimyr@v1
        with:
          checkout: 'false'                     # you already checked out
          threshold: '85'
          # sonar_url: https://sonar.example.com
          # sonar_token: ${{ secrets.SONAR_TOKEN }}
```

The action checks out with `fetch-depth: 0` by default (patch coverage needs the
merge-base). Set `checkout: 'false'` if you already checked out with full history.

### 3. pre-push hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/MagmaMoose/brimyr
    rev: v1.0.0
    hooks:
      - id: brimyr
```

```sh
pre-commit install --hook-type pre-push
git push          # runs the tests + patch-coverage gate against the default branch
```

It runs at **pre-push** (not pre-commit): patch coverage means running the test
suite, too heavy for every commit but fine before a push.

## Patch-coverage semantics

A line counts toward patch coverage iff it is **changed by the PR** (in an
added/modified hunk on the new side, diffed against `merge-base(base, head)`)
**and** the coverage tool considers it **executable**.

| Case | Behaviour |
| --- | --- |
| New file | every executable line counts |
| Modified hunk | only the changed executable lines count |
| Pre-existing uncovered line in a changed file | excluded — never penalised |
| Blank line / comment / brace | excluded (not in the coverage report) |
| Changed file the report doesn't mention (a doc, an untested new file) | contributes nothing (diff-cover behaviour) |
| Deleted file | dropped |
| Nothing coverable changed (docs-only PR) | **vacuous pass** (100%) |
| Broken / empty test run | **tool error (exit 2)**, not 0% |
| Missing merge-base / shallow clone | **fails loudly** — needs `fetch-depth: 0` |

Coverage-report paths and `git diff` paths rarely match byte-for-byte (absolute vs
repo-relative, monorepo prefixes), so matching falls back from exact to suffix
matching; pass `strip_prefix` to peel known roots.

## SonarQube

Optional and non-blocking. Set `sonar_url` (and pass `SONAR_TOKEN`) and Brimyr runs
`sonar-scanner` after computing the gate, pointing Sonar at the coverage file(s) it
already produced (`sonar.python.coverage.reportPaths`,
`sonar.javascript.lcov.reportPaths`). **A Sonar failure never fails the gate** — a
missing binary, a bad URL, or an outage is logged and the run continues.

> .NET coverage → Sonar needs the dedicated *SonarScanner for .NET* (begin/end);
> Brimyr's patch-coverage gate works from the Cobertura file directly regardless.

## CLI

```sh
# Pure: compute patch coverage from a ready-made report + base/head, then gate.
brimyr coverage --coverage-file coverage.xml --base "$BASE" --threshold 80

# Full flow: detect ecosystem, run tests with coverage, gate, ship to Sonar.
brimyr ci --mode auto --sonar-url https://sonar.example.com --sonar-project-key my-svc

# Local pre-push check against the default branch.
brimyr local
```

Exit codes: `0` pass · `1` patch coverage below threshold · `2` broken test run /
setup error.

## Modes

- **PR events** → run tests → **patch-coverage gate** → ship to SonarQube.
- **Push to default branch / scheduled** → run tests → ship to SonarQube as the
  trend baseline → **no** gate.

`mode: auto` (default) picks this from the event; force with `mode: pr|baseline`.

## Conventions

Python (uv + Ruff + pytest, type-hinted, stdlib-only core). External actions are
SHA-pinned. Releases are automated (Diatreme + python-semantic-release). MIT.

## License

MIT. See [LICENSE](LICENSE).
