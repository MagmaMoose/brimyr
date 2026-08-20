# CLAUDE.md

Brimyr is a **patch-coverage gate**. It auto-detects the repo's ecosystem
(Python / JS-TS / .NET), runs the right test command **with coverage on**, and on
a PR gates **only on the coverage of the lines the diff changed** (diff-cover
style, default 80%); pre-existing uncovered code never blocks. Non-blocking, the
same run drives `sonar-scanner` for the SonarQube trend, and posts one PR comment.
One `brimyr` CLI backs two surfaces: `action.yml` and `.pre-commit-hooks.yaml`.

@.claude/QUICK_START.md
@.claude/ARCHITECTURE_MAP.md
@.claude/COMMON_MISTAKES.md

## Conventions

Python ≥ 3.11, **uv + Ruff + pytest**, full type hints, stdlib-only core (lcov by
hand, Cobertura via `xml.etree`). Tests mirror modules 1:1 under `tests/`.
SHA-pin external GitHub Actions with a `# vX.Y.Z` comment. MIT.

**Exit codes:** `0` pass · `1` patch coverage below threshold · `2` broken run /
setup / usage error.

**Releases are automated** — Diatreme + python-semantic-release on push to `main`
cut `vX.Y.Z` from conventional commits and bump both version files. Never by hand.
`release.yml` is provisioned by caldrith: tune it from `MagmaMoose/admin`, not here.

## [cost]

**The AWS bill is paid out of one person's salary. Every infrastructure setting is
a spend control; default to the cheapest thing that works.** There is no hard spend
cap in AWS — a budget *alarms*, it does not stop anything, so the API Gateway
throttle is the broker's only real cap. Before adding or resizing anything
billable, compute the worst case at that ceiling and write it next to the setting.
Details and the free-tier facts: `.claude/COMMON_MISTAKES.md`, `broker/README.md`.

## Context

- Before locating unfamiliar code, read `./PROJECT_INDEX.json`.
- Load `.claude/decisions/` and `.claude/sessions/` ONLY when the task relates to
  them, never by default.
- `./docs` is published human documentation; `.claude/*.md` is terse agent
  context. Keep the two distinct.

## [tooling]

- Prefer targeted line-range reads over whole files; find the location via
  `PROJECT_INDEX.json` first. grep/glob: matched paths and lines only.
- Pipe noisy commands through `head`/`tail`/`grep`, or redirect to
  `.claude/last_output.txt` and read ranges. Never paste thousands of lines.
- After a successful write/edit, trust it; don't re-read to "verify".

## [maintenance]

- Bug that cost >1h → `.claude/COMMON_MISTAKES.md`. Architectural decision →
  `/adr`. Public behaviour/API/config changed → `/update-docs`.
- `PROJECT_INDEX.json` stale after a new module or big refactor: regenerate only
  the affected modules section.
- Keep this file under ~500 tokens and the auto-loaded set (it plus its
  @-imports) under ~1000; push detail into on-demand `.claude/` files.
