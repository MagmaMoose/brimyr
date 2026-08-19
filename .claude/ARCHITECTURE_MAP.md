# Architecture map

A **pure core** with thin **side-effecting edges**.

`src/brimyr/coverage/` is the pure core: `diff.py` parses a unified diff into a
`DiffIndex`; `lcov.py` / `cobertura.py` parse reports into a `CoverageReport`
(`model.py`); `patch.py` intersects the two — added lines ∩ executable lines —
and is the gate's heart. Nothing under `coverage/` may import `subprocess`, `os`,
network code, or GitHub Actions; that purity is what keeps it deterministic and
trivially testable.

The edges inject their runner, so they test without a real toolchain: `git.py`
(the only git boundary), `runner.py` (run tests, ingest), `sonar.py` and
`github_comment.py` / `broker_client.py` (all failure-isolated — never fail the
gate). Then `detect.py` (markers → `Ecosystem`), `gate.py` (percentage → verdict),
`modes.py` (PR vs baseline), `report.py`, `local.py`.

`cli.py:_run_flow` is the whole product in ~40 lines — read it first. Per-module
detail: `./PROJECT_INDEX.json`. Human docs: `./docs/architecture.md`.
