# Subsystem footguns

Read the section for the area you are touching. Always-applicable rules live in
`COMMON_MISTAKES.md` (auto-loaded); these are here because they only bite in one place.

## Ecosystem detection (`detect.py`)

Markers alone over-detect, and over-detection is not a cosmetic problem: it runs a test
command in a repo that has no suite, gets an empty report, and the broken-run rule then
correctly turns the build red on a repo that was never broken. Every over-broad marker
set therefore carries a `confirm` predicate, and an explicit `--ecosystem` bypasses all
of them (forced intent is never downgraded to a skip).

- **`pyproject.toml` is not evidence of a test suite.** It is the most over-broad marker
  in the table — a docs build, a pre-commit pin list and a Terraform repo's tooling all
  ship one — and detecting python off the bare marker runs `pytest --cov`, which exits 5
  with "no tests ran" and an empty report. JavaScript got `_js_has_test_signal` and Java
  got `_java_is_maven` for exactly this reason; python was the one marker set that never
  got the guard. `_python_has_test_signal` looks for a pytest config **section** (not the
  file: `tox.ini` and `setup.cfg` are themselves markers, so their presence proves
  nothing) or a real `test_*.py` / `*_test.py`, pruning vendored directories — because
  `brimyr local` runs against a working tree where one `test_*.py` under
  `.venv/…/site-packages` would make every repo on earth look tested.
- **Detecting nothing is GREEN, and that is the whole adoption story.** `no ecosystem
  detected` used to be the loudest possible error, exit 2 — one line that is why Brimyr
  had to be adopted one repo at a time instead of provisioned like Chargate: every
  charts / Terraform / prompts / docs repo in the org would have gone permanently red on
  a gate it can never satisfy. `no_ecosystem` is now its own state on `GateDecision`:
  green, `gate_result=skipped`, and the summary **replaces** the coverage table rather
  than printing `100% · 0/0 lines`, which is indistinguishable from a well-tested PR.
  Never let those two renderings converge — a repo that quietly lost detection has to
  look different from one that passed. The warning is loud on purpose.
- **Gradle is a marker but is not auto-run.** `build.gradle` makes a repo *recognised* as
  Java (so a detection failure can name it), but the built-in command is `mvn`, so
  `_java_is_maven` requires a `pom.xml`. Gradle users pass `test_command` +
  `coverage_file`; the JaCoCo parser is shared, only the invocation differs.

## Dependency provisioning (`provision.py`)

- **The gate installs the repo's test dependencies; the consumer's workflow does not.**
  Brimyr is provisioned org-wide from one `quality.yml` that knows nothing about any
  repo's toolchain, so anything requiring a per-repo setup step is not provisionable.
  `plan()` maps repo shape → dependency manager: `uv.lock` / `[project]` / `[tool.uv]` →
  `uv run --with pytest-cov <cmd>`; `requirements*.txt` alone → `uv run
  --with-requirements`; `[tool.poetry]` with **no** `[project]` (pre-2.0 layout, which uv
  reads as having zero dependencies) → `poetry install` + `poetry run`; `package.json`
  with no `node_modules` → `npm ci || npm install`. Maven and .NET restore themselves.
- **`--with pytest-cov` is not optional and is not the repo's dependency.** `uv sync`
  alone leaves a perfectly good pytest suite dying on `unrecognized arguments: --cov`.
  It also drags `pytest` in, covering a repo with test files that never declared the
  runner. Injected into the run, never into the repo's lockfile.
- **`action.yml` installs `uv`, or the whole Python half silently declines.** The hosted
  runner images ship none, `plan()` returns an empty plan, and the `pytest: not found`
  red gate comes straight back. It goes into brimyr's own venv and is exposed through a
  directory holding *only* a `uv` symlink, APPENDED to PATH — the venv's own bin would
  shadow the consumer's `python` and `pip` for their entire test run, and a consumer's
  own `uv` must keep winning.
- **Never install into the ambient interpreter.** A gate has no business mutating the
  environment its caller's other steps use, and `brimyr local` has none mutating a
  developer's. Everything goes through the repo's own manager or an ephemeral env.
- **A failed setup command is a broken run, not a test failure and not 0%.** The tests
  never ran, so the suite must not be started against a half-installed environment.
- **An explicit `test_command` disables provisioning.** The caller said how to run the
  tests; wrapping that in a manager they did not ask for changes what they asked to run.

## Coverage formats & report merging (`coverage/`, `runner.py`)

Every trap here has the same shape: a file silently leaves the denominator, and the gate
returns a comfortable number over code nobody measured.

- **`.xml` is not a format.** Cobertura and JaCoCo share the extension, and parsing
  JaCoCo as Cobertura yields an *empty* report → vacuous 100%. `cli._sniff_xml_format`
  picks by root element instead. Never re-add `.xml` to `_EXT_FORMAT`.
- **Total coverage is not `sum(report.files)`.** `merge_reports` keys by the exact path
  string, so one file rooted two ways (a multi-project .NET solution) counts twice —
  measured 50% where the truth was 100%. `compute_total_coverage` is the one to use: it
  folds by path suffix and applies `exclude_globs`. An empty denominator is `None`, never
  the gate's vacuous 100%.
- **JaCoCo paths carry no module prefix.** JaCoCo names a file `<package>/<sourcefile>`,
  so in a multi-module reactor two modules' `nl/x/Service.java` are the *same string*;
  `merge_reports` folds them covered-wins and the covered module answers for the
  uncovered one. Measured 100% where the truth was 0%.
  `runner._jacoco_path_resolver` reconstructs `<module>/<src-root>/…` by walking UP from
  the report and testing each ancestor against the conventional source roots — not by
  stripping known directory names, which stops dead on Gradle's `test` directory
  (`<module>/build/reports/jacoco/test/…` is four levels up, Maven's
  `<module>/target/site/jacoco/…` is three). Anything that does not resolve is returned
  unchanged, so an odd layout degrades rather than inventing a path matching nothing.
- **Take ALL the reports, not the newest.** `dotnet test` on a solution writes one
  `TestResults/<guid>/coverage.cobertura.xml` per test project;
  `locate_coverage_files` returns every match, sorted by path so two runs merge
  identically. Ingesting only the first drops the rest, and a dropped project's files
  read as "nothing coverable changed" rather than as an error.
- **A report that parses but names zero files is not coverage.** It is the coverage tool
  having instrumented nothing, so `RunOutcome.ok` requires `bool(report)`, not
  `report is not None`. The usual JVM cause is a surefire `<argLine>` that overrides
  rather than appends `@{argLine}`, silently detaching the JaCoCo agent.

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

- `coverage_file` supplies Sonar's coverage paths by FORMAT (`_sonar_paths_for_specs`).
  Cobertura is deliberately not guessed: Python and .NET use different properties, and
  the wrong one is silently ignored by Sonar. It warns and names `sonar_args` instead.
- The `action.yml` scanner-install test MUST match `detect.py`'s .NET markers exactly,
  extensions and depth. When they disagree the job installs one scanner and runs the
  other: `skipped (not found on PATH)` on a green build.

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
- **`docs/` may not contain an em-dash or en-dash.** `ci.yml`'s "Docs voice" step is a
  bare `grep -rn '[—–]' docs/` and fails the `test` check on a hit. It covers `docs/`
  ONLY: prose in `.claude/*.md`, `CLAUDE.md`, `AGENTS.md` and the runtime strings in
  `cli.py` use them freely, which is exactly why it is easy to write a docs page in the
  house voice and fail. Reach for a colon, a comma, parentheses, or two sentences. Run
  the grep before pushing docs; `mkdocs build --strict` and markdownlint both pass a
  page that this gate rejects.

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
  skips every read and reports a tool error. Same rule as a broken test run. Either that
  flag or `--quality-counts` turns the quality half on in `_run_flow_inner`.
- **An exit-0 scan is not proof of a COMPLETE one.** chargate declines to start a linter
  it has no image for (or that emits no SARIF) and still exits 0, so those findings are
  simply absent — which is what a clean repo looks like. `linters_skipped` rides in on
  `--quality-scan-note` (`--scan-note` on `brimyr lint`) and is stated next to the count.
  It never gates: a smaller scan is not a failed one, but its number is not the whole
  answer either.
- **`fail_on` defaults to `none`, i.e. report-only, and the summary says so out loud.**
  MegaLinter's quality half over a mature repo is far denser than its security half, and
  a first PR going red with hundreds of findings is how a gate becomes decoration. The
  `quality_fail_on` output exists because report-only and passing both print `pass`.
- **The pinned chargate ref must contain the `quality` flavor.** `action.yml` pins
  `528a42e` (v2.11.27), which does. v1.9.0 shipped against v2.11.25, which did not, and
  on that pin the nested step fails and brimyr reports a broken scan — exit 2, not a
  clean quality half. Re-check this after any Dependabot bump that moves the pin
  backwards. The flavor is synthetic: there is no `megalinter-quality` image, so
  chargate runs the five standalone linters on every architecture.
- **Two comment markers, one comment each.** `brimyr ci --quality-counts` renders both
  verdicts into ONE body under `SUMMARY_MARKER` (`<!-- brimyr:pr-summary -->`) — that
  consolidated view is the reason the flag exists, so prefer it, and it is what the
  action does. Standalone `brimyr lint` owns `QUALITY_MARKER`
  (`<!-- brimyr:quality-summary -->`), because a shared marker would mean whichever
  subcommand ran last erased the other's verdict. Inside the consolidated body the two
  blocks are `## Brimyr: Quality Assurance` (coverage) and `## Brimyr: Net-new findings`
  (quality) — sibling headings, deliberately not nested.
- **The process exit code is the worse of the two halves** on the shared `0 < 1 < 2`
  scale: clean coverage never launders a blocking quality finding, and a broken test run
  still reports 2 when quality is clean.
- In prose, never call this "the quality gate" — that phrase is already SonarQube
  vocabulary in `./docs`. It is "quality findings", or "the quality half".

## `tests/fixtures/diff_corpus/` — shared with chargate

Editing a fixture breaks `test_corpus_checksum_matches` **deliberately**: update
`CORPUS.sha256` *and* copy the change to chargate. The duplicated diff parsers are the
design (brimyr#33); this corpus is the tripwire that replaces the coupling.
