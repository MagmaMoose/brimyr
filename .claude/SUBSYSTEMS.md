# Subsystem footguns

Read the section for the area you are touching. Always-applicable rules live in
`COMMON_MISTAKES.md` (auto-loaded); these are here because they only bite in one place.

## SonarQube (`sonar.py`, `sonar_dotnet.py`)

- **.NET must WRAP build+test** (`sonar_dotnet.session`): the CLI scanner cannot analyze
  C#/VB.NET at all, so `begin` → `dotnet build --no-incremental` → tests → `end`.
  `--no-incremental` is load-bearing — a cached build compiles nothing, the injected
  Roslyn analyzers never run, and `end` uploads an empty analysis from a green job.
- The property is `sonar.cs.cobertura.reportsPaths` — **plural**, unlike the Python and
  JS ones. The singular form is silently ignored.
- Sonar needs an installed scanner *and* a project key. Both were missing for months, so
  the leg reported `skipped (not found on PATH)` while builds stayed green. Any new sink
  must warn via `::warning::`, never bare stderr, or nobody finds out.
- Java is wired but requires `sonar.java.binaries` via `sonar_args`; without it the run
  is skipped with a warning rather than failing.
- Never pass `sonar.branch.name` / `sonar.pullrequest.*` to a Community Build server —
  that is a hard scanner error, not a no-op.

## `broker/` — a separate project

- Its own deps, ruff config (`py312`) and CI job. Root ruff **excludes** it and root
  pytest **ignores** it: `uv run pytest -q` at the root does not test the broker.
  Run `make -C broker test`.
- Gate coverage for it comes from `.github/workflows/coverage.yml`, which runs the suite
  **from the repo root** so paths are `broker/app/*.py` and match the diff exactly.

## Cost — a correctness property

The numbers behind the `[cost]` rule in `CLAUDE.md`:

- `memory_size` >512 MB pushes Lambda compute out of the always-free 400k GB-s under
  load; `throttle_rate_limit` raises the ceiling linearly. Sustained abuse is ~$2.91/mo
  at 1 rps / 512 MB versus ~$16.81 at the module defaults.
- The broker account has **no 12-month free tier**: API Gateway and S3 bill from unit
  one, while Lambda, Logs, SSM, ACM and SNS are always free.
- A two-label hostname costs ~$10/mo — Cloudflare Universal SSL covers the apex and one
  label only, which is why it is `broker-brimyr`, not `broker.brimyr`.
- Full detail: `broker/README.md`.

## CI gates (chargate, CodeQL)

- **Multi-rule `# nosec` is SPACE-separated**, never comma: `# nosec B603 B607`.
  `# nosec B603,B607` is silently invalid — it suppresses nothing and chargate still
  blocks. Verified against bandit directly.
- Keep a suppression on a line short enough that `ruff format` cannot wrap it. A wrapped
  comment lands on a different line from the finding and silently stops applying. Prefer
  a named constant over a literal so the finding never fires.
- chargate gates on **net-new** findings only, so a stale inline thread from an earlier
  push can block a PR after the code is already fixed — re-check the run, then resolve.

## `tests/fixtures/diff_corpus/` — shared with chargate

Editing a fixture breaks `test_corpus_checksum_matches` **deliberately**: update
`CORPUS.sha256` *and* copy the change to chargate. The duplicated diff parsers are the
design (brimyr#33); this corpus is the tripwire that replaces the coupling.
