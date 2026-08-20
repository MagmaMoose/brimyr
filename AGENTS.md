# AGENTS.md

**`CLAUDE.md` is canonical.** This file restates the same rules for agents that do not
read it. There is no `@`-import mechanism here, so the two drift unless edited together —
change one, change the other.

Brimyr is a **patch-coverage gate**: it detects the ecosystem (Python / JS-TS / .NET /
Java), runs its tests **with coverage on**, and gates a PR **only on the lines the diff
changed** (diff-cover semantics, default 80%). Pre-existing uncovered code never blocks.
Non-blocking alongside: total coverage, one PR comment, a SonarQube analysis. One CLI,
two surfaces: `action.yml` and `.pre-commit-hooks.yaml`.

**Exit codes:** `0` pass · `1` below threshold · `2` broken run / setup / usage error.

## Commands

```bash
uv sync                                # install deps + dev tools
uv run pytest -q                       # full suite
uv run ruff check .                    # lint
uv run ruff format .                   # format (CI runs --check)
uv run brimyr local                    # gate this branch vs the default branch
make -C broker test                    # the broker is a SEPARATE project
uv run --group docs mkdocs build       # render ./site (gitignored)
```

## Rules

- **Zero runtime dependencies.** `dependencies = []` is a stated design property; adding
  one ends it.
- **Never hand-bump the version.** python-semantic-release writes `pyproject.toml` and
  `src/brimyr/__init__.py` on push to `main`. `release.yml` is provisioned by caldrith —
  tune it from `MagmaMoose/admin`.
- **Cost is a correctness property.** The AWS bill comes out of one person's salary.
  Before adding or resizing anything billable, compute the worst case at the throttle
  ceiling and write it beside the setting.
- **Nothing under `src/brimyr/coverage/` may do I/O** — no `subprocess`, `os`, network or
  Actions code. That purity is the design.
- Python ≥ 3.11, **uv + Ruff + pytest**, full type hints. Tests mirror modules 1:1
  under `tests/`. SHA-pin external Actions with `# vX.Y.Z`. MIT.

## The failure mode to know

**Bugs here are silent passes, not crashes.** An unmatched path, a report parsed as the
wrong format, or a dropped report in a multi-project run each removes files from the
denominator and returns a comfortable number over untested code. A broken test run is
exit `2`, never 0% coverage. Check the denominator before believing a good result.

## Context files

- `./PROJECT_INDEX.json` — locating unfamiliar code. Read by path, never imported.
- `.claude/COMMON_MISTAKES.md` — always-applicable footguns.
- `.claude/SUBSYSTEMS.md` — Sonar, `broker/`, cost, CI gates, the shared diff corpus.
  Read before touching any of them.
- `.claude/ARCHITECTURE_MAP.md` — the pure-core/edges shape.
- `./docs` is published human documentation; `.claude/*.md` is terse agent context.
