# CLAUDE.md

Brimyr is a **patch-coverage gate**. It auto-detects the repo's ecosystem
(Python / JS-TS / .NET), runs the right test command **with coverage on**, and on
a PR gates **only on the coverage of the lines the diff changed** (diff-cover
style, default 80%); pre-existing uncovered code never blocks. Non-blocking, the
same run drives `sonar-scanner` to ship quality + coverage to SonarQube. One
`brimyr` CLI backs three surfaces: `action.yml` (composite action),
`.github/workflows/gate.yml` (reusable workflow), and `.pre-commit-hooks.yaml`
(local pre-push hook).

@.claude/QUICK_START.md
@.claude/ARCHITECTURE_MAP.md
@.claude/COMMON_MISTAKES.md

## Conventions

Python ≥ 3.11, **uv + Ruff + pytest**, full type hints, stdlib-only core (lcov by
hand, Cobertura via `xml.etree`). Tests mirror modules 1:1 under `tests/`.
SHA-pin external GitHub Actions with a `# vX.Y.Z` comment. MIT.

**Exit codes:** `0` pass · `1` patch coverage below threshold · `2` broken run /
setup / usage error.

**Releases are automated** — pushing to `main` runs Diatreme +
python-semantic-release (`.github/workflows/release.yaml`), which cuts the next
stable `vX.Y.Z` from conventional commits and bumps `project.version` +
`__init__.__version__`. Never bump those by hand.

## Context

- Before locating unfamiliar code, read `./PROJECT_INDEX.json`.
- Load `.claude/decisions/` and `.claude/sessions/` ONLY when the task relates to
  them, never by default.
- `./docs` is the full human documentation (MkDocs, published); `.claude/*.md` is
  terse agent context. Keep the two surfaces distinct.

## [tooling]

- Prefer targeted line-range reads over whole files; use `PROJECT_INDEX.json` to
  find the location first.
- grep/find/glob: return matching paths and matched lines only, not whole files.
- Commands that can flood output: pipe through `head`/`tail`/`grep`, or redirect
  to `.claude/last_output.txt` and read ranges. Don't paste thousands of lines.
- After a successful write/edit, trust it; don't re-read just to "verify".

## [maintenance]

- Bug that took >1h: append to `.claude/COMMON_MISTAKES.md`.
- Architectural decision: run `/adr`.
- Public behaviour/API/config/setup changed: run `/update-docs`.
- `PROJECT_INDEX.json` stale (new module, big refactor): regenerate the affected
  modules section only.
- Keep `CLAUDE.md` under ~500 tokens; push detail into on-demand `.claude/` files.
