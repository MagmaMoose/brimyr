# CHANGELOG

<!-- python-semantic-release manages this file on release; see
.github/workflows/release.yaml. The entry below is the pre-1.0 seed. -->

## v0.1.0 (unreleased)

### Features

- Initial Brimyr: a **patch-coverage gate**.
  - Auto-detects the repo's ecosystem (Python / JavaScript-TypeScript / .NET) and
    runs the right test command with coverage instrumentation on.
  - Computes **patch coverage** — the % of *changed executable lines* the tests
    covered, diffed against the merge-base (diff-cover style) — and **blocks**
    below a threshold (default 80%). Computed locally; no SonarQube involvement.
  - Runs `sonar-scanner` **non-blocking** to ship quality + coverage to SonarQube
    for the trend. A Sonar outage never fails the gate.
  - Parses **lcov** and **Cobertura** coverage; merges one report per language for
    polyglot repos.
  - **Escape hatch:** ingest a pre-made coverage file instead of running tests.
  - **Broken test run ≠ 0% coverage** — a failed/empty run is a tool error (build
    red), never a hard 0% gate failure.
  - Three surfaces over one `brimyr` CLI: reusable workflow, composite action, and
    a local pre-push hook.
