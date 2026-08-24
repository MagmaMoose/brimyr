# AGENTS.md

**`CLAUDE.md` is canonical.** This file restates the same rules for agents that do not
read it. There is no `@`-import mechanism here, so the two drift unless edited together —
change one, change the other.

Brimyr is **quality assurance**: two gates in one run, one summary, one PR comment, and
the job exits on the worse of the two. Chargate is the *security* sibling — the split is
the subject, not the tool. One CLI, two surfaces: `action.yml` and
`.pre-commit-hooks.yaml`.

1. **Patch coverage** — detects the ecosystem (Python / JS-TS / .NET / Java), runs its
   tests **with coverage on**, and gates a PR **only on the lines the diff changed**
   (diff-cover semantics, default 80%). Pre-existing uncovered code never blocks.
   Non-blocking alongside: total coverage and a SonarQube analysis.
2. **Net-new quality findings** — brimyr does not lint: it calls `chargate filter-sarif`
   across a process boundary and gates on the counts JSON that run writes (`quality.py`,
   `brimyr lint`, or `brimyr ci --quality-counts` to fold the verdict into the one
   summary and the one PR comment coverage already writes). Off unless `quality: 'true'`;
   `quality_fail_on` defaults to `none`, so report-only until it names a SARIF level
   (`note`/`warning`/`error`/`any`); `.claude/SUBSYSTEMS.md` before you touch it.

**Exit codes:** `0` pass · `1` below threshold / blocking findings · `2` broken run /
setup / usage error.

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
  Actions code. That purity is the design. `quality.py` holds to it too: `cli.py` reads
  chargate's files, `quality.py` only decides.
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
- `.claude/SUBSYSTEMS.md` — Sonar, `broker/`, cost, the quality half, CI gates, the
  shared diff corpus. Read before touching any of them.
- `.claude/ARCHITECTURE_MAP.md` — the pure-core/edges shape.
- `./docs` is published human documentation; `.claude/*.md` is terse agent context.
