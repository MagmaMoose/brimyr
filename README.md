# Brimyr

[![CI](https://github.com/MagmaMoose/brimyr/actions/workflows/ci.yml/badge.svg)](https://github.com/MagmaMoose/brimyr/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/MagmaMoose/brimyr?sort=semver&logo=github)](https://github.com/MagmaMoose/brimyr/releases)
[![GitHub Marketplace](https://img.shields.io/badge/Marketplace-Brimyr-2ea44f?logo=github)](https://github.com/marketplace/actions/brimyr)
[![Docs](https://img.shields.io/badge/docs-brimyr-purple)](https://magmamoose.github.io/brimyr/)
[![License](https://img.shields.io/github/license/MagmaMoose/brimyr)](LICENSE)

> **Quality assurance for a pull request: gate on the coverage of the lines it changed,
> and the findings it introduced.**

Point Brimyr at any repository and it works out the rest. It detects the ecosystem, runs
the test suite with coverage instrumentation on, and gates on the coverage of the diff
rather than the whole codebase. No per-repo configuration, no server, no SaaS account,
and no coverage report you have to produce first.

**[Documentation](https://magmamoose.github.io/brimyr/)** ·
[Setup](https://magmamoose.github.io/brimyr/setup/) ·
[Action reference](https://magmamoose.github.io/brimyr/action/) ·
[Marketplace](https://github.com/marketplace/actions/brimyr)

## Quickstart

```yaml
# .github/workflows/quality.yml
name: Quality
on: [pull_request]

permissions:
  contents: read
  pull-requests: write   # only needed for pr_comment

jobs:
  brimyr:
    runs-on: ubuntu-latest
    steps:
      - uses: magmamoose/brimyr@v1     # that is the whole configuration
        with:
          pr_comment: 'true'           # off by default
```

## What it does

- **Runs your tests** — detects the ecosystem and turns coverage instrumentation on
  itself. Every other patch-coverage tool asks you to bring your own report, and across
  eight repos in four languages that wiring *is* the project.
- **Gates on the diff** — the coverage of the lines this PR changed, matching
  [diff-cover](https://github.com/Bachmann1234/diff_cover) semantics deliberately. A
  large existing gap never blocks anyone.
- **Gates on net-new findings** — with `quality: 'true'`, classifies linter findings
  against that same diff via [Chargate](https://github.com/MagmaMoose/chargate).
- **One consolidated PR comment** — updated in place, never stacked. Opt in with
  `pr_comment: 'true'`.
- **Ships the trend** — optional SonarQube export, without letting it gate.
- **Runs entirely on your runner** — free, MIT, no backend.

> **Brimyr executes the pull request's own test code on the runner.** That is inherent
> to running tests, and it is why the default workflow trigger is `pull_request` rather
> than `pull_request_target`: the former gives a fork's code no access to your secrets.
> Do not change that without reading
> [Setup](https://magmamoose.github.io/brimyr/setup/).

## Most-used inputs

| Input | Default | What it does |
| --- | --- | --- |
| `threshold` | `80` | Patch-coverage percentage that blocks below it. |
| `quality` | `false` | Also gate on net-new quality findings, running Chargate as a nested step. |
| `ecosystem` | auto-detected | Force one or more: `python`, `javascript`, `dotnet`, `java`. |
| `mode` | `auto` | `auto` from the event · `pr` (diff gate) · `baseline`. |
| `pr_comment` | `false` | Post one consolidated PR comment. Needs `pull-requests: write`. |
| `sonar_url` | — | Set to export the trend to SonarQube. |

All inputs and outputs →
**[Action reference](https://magmamoose.github.io/brimyr/action/)**

## Documentation

| | |
| --- | --- |
| [Setup and usage](https://magmamoose.github.io/brimyr/setup/) | Workflows, the pre-push hook, thresholds |
| [Patch coverage](https://magmamoose.github.io/brimyr/patch-coverage/) · [Quality findings](https://magmamoose.github.io/brimyr/quality-findings/) | What counts as covered, and what counts as net-new |
| [.NET](https://magmamoose.github.io/brimyr/dotnet/) · [Java / JVM](https://magmamoose.github.io/brimyr/java/) | Per-ecosystem notes |
| [Action reference](https://magmamoose.github.io/brimyr/action/) · [CLI reference](https://magmamoose.github.io/brimyr/cli/) | Every input, output and command |
| [Architecture](https://magmamoose.github.io/brimyr/architecture/) · [Troubleshooting](https://magmamoose.github.io/brimyr/troubleshooting/) | How it works, and what to do when it doesn't |

## Where it sits

**Brimyr** gates tests and coverage · [Chargate](https://github.com/MagmaMoose/chargate)
gates security and lint · [Diatreme](https://github.com/MagmaMoose/diatreme) releases
what passes · [Tremvok](https://github.com/MagmaMoose/tremvok) deploys and verifies

Brimyr and Chargate answer different questions about the same diff. Chargate classifies
the findings; Brimyr decides whether the PR is adequately tested, and can fold Chargate's
verdict into its own.

## Versioning

Pin `@v1` for the floating major, or a tag or SHA to freeze.

## Security · Contributing · License

[Report a vulnerability](https://github.com/MagmaMoose/brimyr/security/advisories/new) ·
[Contributing](https://github.com/MagmaMoose/.github/blob/main/CONTRIBUTING.md) ·
Apache-2.0, see [LICENSE](LICENSE).
