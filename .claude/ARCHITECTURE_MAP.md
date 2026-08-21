# Architecture map

A **pure core** with thin **side-effecting edges**.

`src/brimyr/coverage/` is the pure core: `diff.py` parses a unified diff into a
`DiffIndex`; `lcov.py` / `cobertura.py` / `jacoco.py` parse reports into a
`CoverageReport` (`model.py`); `patch.py` intersects the two — added lines ∩ executable
lines — and is the gate's heart. Nothing under `coverage/` may import `subprocess`, `os`,
network or Actions code; that purity is what keeps it deterministic and testable.

`quality.py` is a second pure module, outside `coverage/`: handed chargate's
already-parsed `filter-sarif` counts JSON it returns a verdict, so `cli.py` does every
read and an unreadable or self-contradicting input is exit 2, never a pass. `cmd_lint`
reaches it directly; `_run_flow` folds it into the same summary, comment and exit code as
coverage when `--quality-counts` is given.

Edges inject their runner, so they test without a real toolchain: `git.py` (the only git
boundary), `runner.py` (run tests, ingest). `sonar.py`, `sonar_dotnet.py`,
`html_report.py`, `github_comment.py` and `broker_client.py` are all **failure-isolated
— they never fail the gate**. Then `detect.py` (markers → `Ecosystem`), `gate.py`
(percentage → verdict), `modes.py` (PR vs baseline), `report.py`, `local.py`.

`cli.py:_run_flow` is the entry to the whole product — read it first. It dispatches to
`_run_flow_wrapped` (.NET only: the Sonar scanner must wrap the build) or straight to
`_run_flow_inner`, which is the real ~70-line flow.

Per-module detail: `./PROJECT_INDEX.json`. Human docs: `./docs/architecture.md`.
