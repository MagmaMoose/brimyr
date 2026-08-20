# Brimyr

[![CI](https://github.com/magmamoose/brimyr/actions/workflows/ci.yml/badge.svg)](https://github.com/magmamoose/brimyr/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/magmamoose/brimyr)](LICENSE)

**Point Brimyr at any repo and it figures out the rest.** It detects the ecosystem,
runs the test suite with coverage instrumentation on, and gates the pull request on
the coverage of the lines that pull request changed. One composite action. No
per-repo configuration, no server, no SaaS account, no coverage report to produce
first.

```yaml
      - uses: magmamoose/brimyr@v1     # that is the whole configuration
```

That is the part nothing else does. Patch-coverage *maths* is well-trodden —
[diff-cover](https://github.com/Bachmann1234/diff_cover) has done it for years and
Brimyr deliberately matches its semantics. What every other tool has in common is
that **you bring your own coverage report**: you work out the right test command for
each language, wire it per repo, and hand the result to a checker. Across eight
repos in four languages, that wiring *is* the project.

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
is **security assurance**. They are twins, not competitors — Chargate gates net-new
security findings, Brimyr gates the coverage of your change, and a repo wants both.
[Diatreme](https://github.com/MagmaMoose/diatreme) builds and releases.

It also does the thing SonarSource's own action declines to: **`SonarSource/sonarqube-scan-action`
explicitly does not support .NET** and tells you to run SonarScanner for .NET
yourself. Brimyr does that for you — `begin` → `dotnet build --no-incremental` →
your tests → `end` — automatically, when it detects a .NET repo.

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

## What the author sees

One comment per pull request, updated in place — both numbers, so the author gets the
picture rather than just a verdict:

```
## 🟣 Brimyr — patch coverage

**Mode:** `pr` · **Gate:** `pass` · **Ecosystem:** .NET, JavaScript / TypeScript

| Metric | Value |
|--------|-------|
| Patch coverage | **92.3%** |
| Covered / changed executable lines | 24 / 26 |
| Total coverage (measured files) | 61.4% |
| Covered / executable lines across 318 file(s) | 8,204 / 13,362 |
| Threshold | 80.0% |

✅ Patch coverage 92.3% meets the 80.0% threshold.
```

The gate is the patch number alone. A codebase at 61% does not fail a well-tested
change — that is the whole point — but the author still sees where the repo stands.

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
# .github/workflows/coverage.yml
name: Coverage
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write   # for the PR comment

jobs:
  coverage:
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
          pr_comment: 'true'                    # one PR comment, updated in place
          # sonar_url: https://sonar.example.com
          # sonar_token: ${{ secrets.SONAR_TOKEN }}
```

On PRs it runs your tests with coverage, gates on patch coverage, and (if
`sonar_url` is set) ships to SonarQube. On push to the default branch it runs a
non-gating baseline that still feeds the trend.

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
```

Exit codes: `0` pass · `1` patch coverage below threshold · `2` broken test run /
setup error.

## Modes

- **PR events** → run tests → **patch-coverage gate** → ship to SonarQube.
- **Push to default branch / scheduled** → run tests → ship to SonarQube as the
  trend baseline → **no** gate.

`mode: auto` (default) picks this from the event; force with `mode: pr|baseline`.

## Documentation

Full docs at [`./docs`](docs/index.md), built with MkDocs.

| Page | What's in it |
| --- | --- |
| [Setup & usage](docs/setup.md) | Wiring the action, the pre-push hook, ingesting a pre-made report |
| [Action reference](docs/action.md) | Every input and output, with real defaults |
| [CLI reference](docs/cli.md) | Every command and flag |
| [Patch coverage](docs/patch-coverage.md) | What counts, what doesn't, and why |
| [SonarQube](docs/sonarqube.md) | The Sonar leg, including the .NET wrap |
| [Troubleshooting](docs/troubleshooting.md) | Symptom, cause, fix |
| [Architecture](docs/architecture.md) | Pure core, side-effecting edges |

## Conventions

Python (uv + Ruff + pytest, type-hinted, stdlib-only core). External actions are
SHA-pinned. Releases are automated (Diatreme + python-semantic-release). MIT.

## License

MIT. See [LICENSE](LICENSE).
