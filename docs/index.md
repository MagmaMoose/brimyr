# Brimyr

Brimyr is a **patch-coverage gate**. On a pull request it auto-detects the repo's
ecosystem, runs the right test command with coverage instrumentation on, and gates
**only on the coverage of the lines the PR changed** (diff-cover style) — blocking
below a threshold (default **80%**). Pre-existing uncovered code never blocks.

The same run, non-blocking, drives `sonar-scanner` to ship quality + coverage to
SonarQube for the long-run trend. It is the coverage sibling of
[Chargate](https://github.com/MagmaMoose/chargate) (net-new security/lint) and
[Diatreme](https://github.com/MagmaMoose/diatreme) (build + release).

## Two faces, kept separate

- **Blocking — the patch-coverage gate.** The percentage of *changed executable
  lines* the tests covered, diffed against `merge-base(base, head)`. Blocks below
  the threshold. Computed **locally**; no SonarQube involvement.
- **Non-blocking — one `sonar-scanner` run.** Sonar's native quality analysis plus
  ingesting the coverage file → SonarQube, for history and the coverage/quality
  trend. Sonar derives new-vs-old code itself (its New Code Period); you never feed
  it "net-new".

!!! danger "Broken test run ≠ 0% coverage"
    If the test run failed, timed out, or produced no coverage, that is an **error
    (build red)** — never reported as "0% patch coverage" that hard-fails the gate.

## Coverage is a byproduct of the test run

You run the tests *with instrumentation on* (`pytest --cov`, `jest --coverage`,
`dotnet test --collect`) and that single run emits the coverage file. There is no
separate "measure coverage" pass. Brimyr detects the ecosystem and runs the right
command; polyglot repos produce **one coverage file per language**, merged.

## Three surfaces, one CLI

| Surface | What it is | When to use |
| --- | --- | --- |
| **Reusable workflow** | `.github/workflows/gate.yml` (`on: workflow_call`) | Easiest — a consumer's whole config is ~one job block. |
| **Composite action** | `action.yml` | When you compose your own steps. |
| **pre-push hook** | `.pre-commit-hooks.yaml` (`brimyr` hook) | Catch a shortfall locally before pushing. |

See [Setup & usage](setup.md) to wire one up, [Architecture](architecture.md) for
how it fits together, and [Patch coverage](patch-coverage.md) for the precise
classification rules.

## Modes

- **PR events** → run tests → patch-coverage gate → ship to SonarQube.
- **Push to default branch / scheduled** → run tests → ship to SonarQube as the
  trend baseline → **no** gate.

`mode: auto` (default) picks this from the event; force it with `mode: pr|baseline`.

## License

MIT.
