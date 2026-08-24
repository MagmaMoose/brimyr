# CLAUDE.md

Canonical agent context. `AGENTS.md` restates it — **edit both together.**

**Quality assurance: two gates, one run, one summary, one PR comment**; the job exits on
the worse of the two. Chargate is the *security* sibling; the split is the subject, not
the tool.

**Patch coverage** — detects the ecosystem (Python / JS-TS / .NET / Java), runs its tests
**with coverage on**, gates **only on the lines the diff changed** (diff-cover, 80%);
pre-existing uncovered code never blocks. **Net-new quality** — brimyr does not lint: it
calls `chargate filter-sarif` across a process boundary and gates on the counts JSON
(`quality.py`, `brimyr lint`). Off unless `quality: 'true'`; `quality_fail_on` defaults
to `none`, so report-only until it names a SARIF level (`note`/`warning`/`error`/`any`).

Non-blocking alongside: total coverage, and a SonarQube analysis when `sonar_url` +
`sonar_token` are set (skipped with a reason otherwise).

@.claude/QUICK_START.md
@.claude/COMMON_MISTAKES.md

**Exit codes:** `0` pass · `1` below threshold / blocking findings · `2` broken run /
setup / usage error.

## Rules

- **Zero runtime dependencies.** `dependencies = []` is a stated design property in four
  files; adding one ends it.
- **Never hand-bump the version.** Diatreme + python-semantic-release cut `vX.Y.Z` from
  conventional commits on push to `main`. `release.yml` is caldrith's — tune it from
  `MagmaMoose/admin`, not here.
- **[cost]: the AWS bill comes out of one person's salary.** Default to the cheapest
  thing that works, and price the worst case before adding anything billable. How, and
  the numbers: `.claude/SUBSYSTEMS.md`.
- Python ≥ 3.11, **uv + Ruff + pytest**, full type hints. Tests mirror modules 1:1
  under `tests/`. SHA-pin external Actions with `# vX.Y.Z`. MIT.

## Context

Pure core (`src/brimyr/coverage/` — no I/O, ever) with failure-isolated edges that never
fail the gate. Start at `cli.py:_run_flow`.

- Locating unfamiliar code → `./PROJECT_INDEX.json` first.
- Shape and module roles → `.claude/ARCHITECTURE_MAP.md`.
- Touching Sonar, `broker/`, cost, the quality half, a CI gate or
  `tests/fixtures/diff_corpus/` → `.claude/SUBSYSTEMS.md` **first**.
- `.claude/decisions/` and `.claude/sessions/` only when the task relates to them.
- `./docs` is published human docs; `.claude/*.md` is terse agent context.

## [tooling]

- Line-range reads over whole files. grep/glob: matched paths and lines only.
- Pipe noisy output through `head`/`tail`/`grep`, or redirect to
  `.claude/last_output.txt` and read ranges.
- After a successful write/edit, trust it; don't re-read to "verify".

## [maintenance]

- Bug that cost >1h → `COMMON_MISTAKES.md`, or `SUBSYSTEMS.md` if area-specific.
- Architectural decision → `/adr`. Public behaviour/API/config changed → `/update-docs`.
- `PROJECT_INDEX.json` stale after a new module or refactor: regenerate that section,
  bump `generated`.
- Auto-loaded tier (this file + @-imports): a ~1500-token target, not a measurement.
  **Measure it** — words×1.3 under-reads this content ~40%. Move detail out, never
  delete it.
