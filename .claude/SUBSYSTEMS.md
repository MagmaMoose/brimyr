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

Every infrastructure setting is a spend control. Before adding or resizing anything
billable, compute the worst case **at the throttle ceiling** and write it beside the
setting. The numbers behind the `[cost]` rule in `CLAUDE.md`:

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

## The quality half (`quality.py`, `brimyr lint`)

Brimyr does not lint. Chargate already owns a finished net-new engine, so brimyr calls
`chargate filter-sarif` across a **process boundary** and gates on what that run leaves
behind (`.claude/decisions/0002-quality-gate-calls-chargate.md`). Nothing here imports
chargate, and the two sides release independently — which is what every rule below is
really about.

- **The counts JSON is the only input to the verdict.** The filtered SARIF is
  display-only: skimmed for `path:line [rule]` strings, capped at 20, never consulted
  for pass/fail. Resolving a level or a severity from it here would be a second copy of
  chargate's classifier, which is the coupling the boundary exists to avoid.
- **A missing or unrecognised `schema_version` is exit 2, never a pass.** So are counts
  that disagree with themselves (`sum(per_level_net_new) != net_new_count`) and a
  filtered SARIF whose result count contradicts `net_new_count`. All three are the local
  shape of the repo-wide rule: a dropped, stale or truncated report otherwise reads as a
  comfortable "0 net-new findings". The other end of the contract is
  `chargate.sarif.COUNTS_SCHEMA_VERSION` — bump the two together, and treat
  `filter-sarif`'s output as the public interface it now is.
- **`fail_on` speaks SARIF *levels*, not chargate's severity bands**: `none`, `note`,
  `warning`, `error`, `any`. Not because chargate's bands are broken — its gate reads
  per-result verdicts, where a missing `security-severity` falls back to the level, so
  `fail_on: high` works fine there. Brimyr has no verdicts, only the counts document, and
  its `per_severity_*` maps are populated **solely** from a real `security-severity`
  property that quality linters essentially never emit. A band threshold read off that
  document matches nothing, forever, while looking configured. `error` is chargate's
  `high`, and there is deliberately no level above it — a `critical` could never fire.
- **A counts file on disk is not proof the scan ran.** `chargate ci` writes it *before*
  it checks whether the scan produced any runs, so a scan that found nothing leaves a
  well-formed row of zeros and only then exits 2. `action.yml` therefore trusts the
  chargate step's `outcome`, not the file, and passes `--quality-scan-broken` — which
  skips every read and reports a tool error. Same rule as a broken test run.
- **`fail_on` defaults to `none`, i.e. report-only, and the summary says so out loud.**
  MegaLinter's quality half over a mature repo is far denser than its security half, and
  a first PR going red with hundreds of findings is how a gate becomes decoration. The
  `quality_fail_on` output exists because report-only and passing both print `pass`.
- **The pinned chargate ref must contain the `quality` flavor.** `action.yml` currently
  pins v2.11.25, which does **not** — `quality: true` only starts working once chargate
  ships that release and the pin here is bumped to it. The flavor is synthetic: there is
  no `megalinter-quality` image, so chargate runs the five standalone linters on every
  architecture.
- **Two comment markers, one comment each.** `brimyr ci --quality-counts` renders both
  verdicts into ONE body under `SUMMARY_MARKER` — that consolidated view is the reason
  the flag exists, so prefer it. Standalone `brimyr lint` owns `QUALITY_MARKER`, because
  a shared marker would mean whichever subcommand ran last erased the other's verdict.
- **The process exit code is the worse of the two halves** on the shared `0 < 1 < 2`
  scale: clean coverage never launders a blocking quality finding, and a broken test run
  still reports 2 when quality is clean.
- In prose, never call this "the quality gate" — that phrase is already SonarQube
  vocabulary in `./docs`. It is "quality findings", or "the quality half".

## `tests/fixtures/diff_corpus/` — shared with chargate

Editing a fixture breaks `test_corpus_checksum_matches` **deliberately**: update
`CORPUS.sha256` *and* copy the change to chargate. The duplicated diff parsers are the
design (brimyr#33); this corpus is the tripwire that replaces the coupling.
