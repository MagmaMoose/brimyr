# Brimyr

[![CI](https://github.com/magmamoose/brimyr/actions/workflows/ci.yml/badge.svg)](https://github.com/magmamoose/brimyr/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/magmamoose/brimyr)](LICENSE)

**Brimyr is quality assurance for a pull request: it gates on the coverage of the
lines that PR changed, and on the net-new quality findings that PR introduced.** Point
it at any repo and it figures out the rest — it detects the ecosystem, runs the test
suite with coverage instrumentation on, and (with `quality: 'true'`) classifies the
linters' findings against that same diff. One composite action, one job, one PR
comment. No per-repo configuration, no server, no SaaS account, no coverage report to
produce first.

```yaml
      - uses: magmamoose/brimyr@v1     # that is the whole configuration
```

That is the part nothing else does. Patch-coverage *maths* is well-trodden —
[diff-cover](https://github.com/Bachmann1234/diff_cover) has done it for years and
Brimyr deliberately matches its semantics. What every other tool has in common is
that **you bring your own coverage report**: you work out the right test command for
each language, wire it per repo, and hand the result to a checker. Across eight
repos in four languages, that wiring *is* the project. Against the other
patch-coverage tools:

| | Runs your tests | Detects the ecosystem | Gates on the **diff** | No SaaS backend |
| --- | :-: | :-: | :-: | :-: |
| **Brimyr** | ✅ | ✅ | ✅ | ✅ |
| diff-cover | ✗ | ✗ | ✅ | ✅ |
| Codecov / Coveralls | ✗ | ✗ | ✅ | ✗ |
| SonarQube Community | ✗ | ✗ | ✗ *(project-level)* | ✗ |
| GitHub Code Quality | ✗ | ✗ | ✗ *(total + delta)* | ✗ |
| `jacoco-report`, `cobertura-action`, … | ✗ | ✗ | ✅ | ✅ *(one language each)* |

Free, MIT, and it runs entirely on your runner.

## Where it sits

Brimyr is **quality assurance**; [Chargate](https://github.com/MagmaMoose/chargate)
is **security assurance**; [Diatreme](https://github.com/MagmaMoose/diatreme) builds
and releases. Brimyr and Chargate are twins, not competitors — Chargate gates net-new
*security* findings, Brimyr gates coverage **and** net-new *quality* findings, and a
repo wants both. The line between them is the **subject**, not the tool: Brimyr's
quality half runs Chargate's net-new engine as a nested step rather than growing its
own — [see below](#coverage-is-half-of-quality-assurance).

It also does the thing SonarSource's own action declines to: **`SonarSource/sonarqube-scan-action`
explicitly does not support .NET** and tells you to run SonarScanner for .NET
yourself. Brimyr does that for you — `begin` → `dotnet build --no-incremental` →
your tests → `end` — automatically, when it detects a .NET repo.

## Three faces, kept separate

| Face | Blocking? | What it is |
| --- | --- | --- |
| **Patch-coverage gate** | **yes** | The % of *changed executable lines* covered, diffed against the merge-base. Blocks below the threshold. Computed **locally** — no SonarQube involvement. |
| **Net-new quality gate** | **report-only by default** | The quality findings *this PR introduced*, classified against that same diff. Off unless `quality: 'true'`, and even then `quality_fail_on` defaults to `none` — it counts and reports, and blocks only at a SARIF level you choose. |
| **`sonar-scanner` run** | no | One Sonar run performs Sonar's native quality analysis **and** ingests the coverage file → SonarQube, for the coverage/quality trend. |

The two gates share one job, one summary and one PR comment, and the job exits on the
worse of the two. Sonar is the odd one out: it derives new-vs-old code itself (its
**New Code Period**), so you never feed it "net-new" coverage. Net-new is the gates'
job, and neither gate needs Sonar.

## Why patch coverage?

Requiring 80% coverage on a whole legacy codebase is a non-starter; ignoring
coverage on new code lets it rot. Patch coverage splits the difference: hold *new
and changed* lines to a bar, leave the back-catalogue alone.

- **Gate** on what *this PR* changed → actionable, no legacy-debt noise.
- **Ship** the full coverage to SonarQube → the long-run trend, and Sonar's own
  quality gate (not to be confused with Brimyr's net-new quality gate above).

## What the author sees

**One** comment per pull request, updated in place, carrying **both halves** — so the
author gets the picture rather than just a verdict:

```
## Brimyr: Quality Assurance

**Mode:** `pr` · **Gate:** `pass` · **Ecosystem:** .NET, JavaScript / TypeScript

| Metric | Value |
|--------|-------|
| Patch coverage | **92.3%** |
| Covered / changed executable lines | 24 / 26 |
| Total coverage (measured files) | 61.4% |
| Covered / executable lines across 318 file(s) | 8204 / 13362 |
| Threshold | 80.0% |

✅ Patch coverage 92.3% meets the 80.0% threshold.

## Brimyr: Net-new findings

**Gate:** `report-only` · **Blocks on:** `none`

| Metric | Value |
|--------|-------|
| Net-new findings | **16** |
| Pre-existing (never blocking) | 214 |
| Net-new by level | error=2, note=3, warning=11 |

📋 Report-only — `quality_fail_on` is `none`, so findings are counted and shown but nothing blocks.
```

Only the patch number is gating there. A codebase at 61% does not fail a well-tested
change — that is the whole point — and at the default `quality_fail_on: none` those 16
net-new findings do not fail it either. Both are shown so the author sees where the
repo stands: a green Brimyr routinely carries findings it did not block on.

Small diffs are exempt and **say so**, rather than passing quietly:

```
⚪ Only 3 changed executable line(s) — below the 20-line minimum, so the 80.0%
threshold was not applied (patch coverage was 33.3%).
```

SonarQube applies the same 20-line rule and says nothing about it, which is how a team
ends up believing small PRs are gated when they are not. `min_lines: '0'` closes it.

## Rollout cost is the point

The comparison that matters is not "which tool computes coverage" — it is what it takes
to switch this on across an estate. For eight repos in four languages:

| | SonarQube | Brimyr |
| --- | --- | --- |
| Server to host / licence | a server, or a per-committer plan | none |
| Per repo | a project, a token, a quality gate, workflow YAML | one `uses:` line |
| Per ecosystem | a *different scanner* — `dotnet sonarscanner` for .NET, `mvn sonar:sonar` for Java, the CLI for the rest | detected |
| Producing the coverage report | yours to work out, per language | it runs your tests |
| Per-PR diff coverage | Developer Edition and above | included |

## Coverage is a byproduct of the test run

You run the tests **with instrumentation on** (`pytest --cov`, `jest --coverage`,
`dotnet test --collect`) and that single run emits the coverage file. There is no
separate "measure coverage" pass. Brimyr auto-detects which command to run:

| Ecosystem | Markers | Test command | Coverage |
| --- | --- | --- | --- |
| **Python** | `pyproject.toml`, `setup.py`, `tox.ini`, … | `pytest --cov --cov-report=xml` | Cobertura |
| **JS / TS** | `package.json` | `jest` — or `vitest run` when the repo uses vitest | lcov |
| **.NET** | `*.csproj`, `*.sln`, … | `dotnet test --collect:"XPlat Code Coverage"` | Cobertura |
| **Java / JVM** | `pom.xml` | `mvn … jacoco:prepare-agent test jacoco:report` | JaCoCo |

Polyglot repos (a JS frontend + a Python backend) match more than one and produce
**one coverage file per language**; Brimyr runs each and merges the reports.

> ⚠️ **Broken test run ≠ 0% coverage.** If the test run fails, times out, or
> produces no coverage, that is a **tool error (build red)** — never reported as
> "0% patch coverage" that hard-fails the gate.

## Two surfaces

| Surface | What it is | When to use |
| --- | --- | --- |
| **Composite action** | `action.yml` | In CI. The one surface, and the one Brimyr gates itself with. |
| **pre-push hook** | `.pre-commit-hooks.yaml` (`brimyr` hook) | Catch a coverage shortfall locally before pushing. |

Both drive the same `brimyr` Python CLI.

### 1. Composite action

```yaml
# .github/workflows/quality.yml
name: Quality
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write   # for the PR comment

jobs:
  quality:
    runs-on: ubuntu-latest
    steps:
      # Check out FIRST: the deps install below runs in the workspace, so the
      # action's own checkout would be too late. `fetch-depth: 0` because patch
      # coverage needs the merge-base.
      - uses: actions/checkout@v6
        with: { fetch-depth: 0 }
      - uses: actions/setup-python@v6
        with: { python-version: '3.12' }
      - run: pip install -e '.[test]'          # your test deps
      - uses: magmamoose/brimyr@v1
        with:
          checkout: 'false'                     # already checked out above
          threshold: '80'
          quality: 'true'                       # the other half; report-only by default
          pr_comment: 'true'                    # one PR comment, both halves, in place
          # sonar_url: https://sonar.example.com
          # sonar_token: ${{ secrets.SONAR_TOKEN }}
```

On PRs it runs your tests with coverage, gates on patch coverage, classifies the
net-new quality findings, and (if `sonar_url` is set) ships to SonarQube. On push to
the default branch it runs a non-gating baseline that still feeds the trend.

Brimyr runs the tests **on the runner**, so install your toolchain and test deps
in a step before it — or skip the run entirely by feeding a ready-made report via
`coverage_file`.

The action checks out with `fetch-depth: 0` by default, so if Brimyr is your only
step you can drop both the checkout and `checkout: 'false'`. Any step that touches
the workspace before it — installing test deps, as above — needs the explicit
checkout, because the action's own would run too late.

### 2. pre-push hook

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

## Coverage is half of quality assurance

The other half is what the linters say, so `quality: 'true'` adds a second gate: the
**net-new quality findings** this pull request introduced, classified against the same
diff the coverage gate uses. Details in [Quality findings](docs/quality-findings.md).

Brimyr implements none of that classification. Chargate already owns a finished net-new
engine, so Brimyr does not import it, vendor it or re-implement it — it **calls it as a
nested step** (MegaLinter's quality linters → SARIF → net-new against the diff) and
reads the two files that run leaves behind. A shared package would buy version skew,
lockfile drift and a diamond dependency inside one job; a subprocess in its own
environment has none of those properties. That is written down as
[ADR 0002](.claude/decisions/0002-quality-gate-calls-chargate.md).

Chargate's own `fail_on` is pinned to `none` so it can never set the job's exit code:
it reports, Brimyr decides. The verdict lands in the *same* job summary and the *same*
PR comment as coverage, and the job exits on the worse of the two.

The nested step is `continue-on-error`, so a Docker or MegaLinter failure cannot take the
coverage gate down with it — and it is not a silent pass either. Brimyr branches on that
step's `outcome`, never on the file it may have left behind: `chargate ci` writes its
counts JSON *before* it knows whether the scan produced any runs, so a failed scan can
leave a well-formed row of zeros on disk, which is exactly what a clean PR looks like. A
scan that did not complete is therefore reported as a tool error — exit `2`, output
`quality_gate_result: error` — and a scan that *did* complete but could not start every
linter says which ones beside the count, rather than passing a smaller scan off as the
whole answer.

`quality_fail_on` defaults to `none` — **report-only, deliberately**. MegaLinter's
quality half over a mature repo is far denser than its security half, and a first PR
that goes red with hundreds of findings is how a gate becomes decoration nobody reads.
Ship it reporting, measure a release cycle, then pick a level: `note`, `warning`,
`error` or `any`. Those are SARIF *levels*, not Chargate's severity bands: Chargate
gates on per-result verdicts, where a missing `security-severity` falls back to the
level, but Brimyr reads only the counts document, whose per-severity maps are populated
solely from that property — which quality linters essentially never emit. A band-valued
threshold read off it would sit there never blocking anything.

## SonarQube

Optional and non-blocking, and it **actually installs a scanner** — set `sonar_url`
plus `SONAR_TOKEN` and that is the whole configuration. The project key defaults to
the repo slug (`owner/repo` → `owner_repo`), because `sonar-scanner` aborts without one.

**A Sonar failure never fails the gate.** An outage, a bad URL or a missing token is a
`::warning::` annotation and the run continues.

Note that this is a full SonarQube **analysis**, not a coverage upload: bugs, code
smells, duplication and security hotspots come along with it whether or not you look at
them. There is no coverage-only mode.

| Ecosystem | Scanner | Property |
| --- | --- | --- |
| Python | `sonar-scanner` (post-step) | `sonar.python.coverage.reportPaths` |
| JS / TS | `sonar-scanner` (post-step) | `sonar.javascript.lcov.reportPaths` |
| Java | `sonar-scanner` (post-step) | `sonar.coverage.jacoco.xmlReportPaths` — **needs `sonar.java.binaries`**, see `docs/java.md` |
| .NET | `dotnet sonarscanner` **wrapping the build** | `sonar.cs.cobertura.reportsPaths` |

.NET is the odd one and cannot be made uniform: SonarSource documents that the
SonarScanner CLI *"doesn't support C# or VB.NET analysis"*, because C# issues come from
Roslyn analyzers injected into the compilation. So for .NET the scanner **wraps** the
run — `begin` → `dotnet build --no-incremental` → your tests → `end` — instead of
following it. Brimyr does that automatically when it detects .NET; nothing to configure.

## CLI

```sh
# Pure: compute patch coverage from a ready-made report + base/head, then gate.
brimyr coverage --coverage-file coverage.xml --base "$BASE" --threshold 80

# Full flow: detect ecosystem, run tests with coverage, gate, ship to Sonar.
brimyr ci --mode auto --sonar-url https://sonar.example.com --sonar-project-key my-svc

# Local pre-push check against the default branch.
brimyr local

# The quality half on its own: gate the net-new findings Chargate classified.
brimyr lint --counts chargate-reports/counts.json --fail-on error
```

Exit codes: `0` pass · `1` patch coverage below threshold, or blocking quality
findings · `2` broken test run / setup error, or a quality scan that did not complete.

## Modes

- **PR events** → run tests → **patch-coverage gate**, plus the **quality gate** when
  `quality: 'true'` → ship to SonarQube.
- **Push to default branch / scheduled** → run tests → ship to SonarQube as the
  trend baseline → **no** gate. Quality findings are still counted and shown; a
  baseline run has no diff to gate against, and says so.

`mode: auto` (default) picks this from the event; force with `mode: pr|baseline`.

## Documentation

Full docs at [`./docs`](docs/index.md), built with MkDocs.

| Page | What's in it |
| --- | --- |
| [Setup & usage](docs/setup.md) | Wiring the action, the pre-push hook, ingesting a pre-made report |
| [Action reference](docs/action.md) | Every input and output, with real defaults |
| [CLI reference](docs/cli.md) | Every command and flag |
| [Patch coverage](docs/patch-coverage.md) | What counts, what doesn't, and why |
| [Quality findings](docs/quality-findings.md) | The quality half: Chargate as a nested step, and what blocks |
| [SonarQube](docs/sonarqube.md) | The Sonar leg, including the .NET wrap |
| [Troubleshooting](docs/troubleshooting.md) | Symptom, cause, fix |
| [Architecture](docs/architecture.md) | Pure core, side-effecting edges |

## Conventions

Python (uv + Ruff + pytest, type-hinted, stdlib-only core). External actions are
SHA-pinned. Releases are automated (Diatreme + python-semantic-release). MIT.

## License

MIT. See [LICENSE](LICENSE).
