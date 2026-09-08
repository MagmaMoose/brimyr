# Troubleshooting

<!-- sources: src/brimyr/git.py, src/brimyr/cli.py, src/brimyr/runner.py, src/brimyr/sonar.py,
     src/brimyr/html_report.py
     -->

Symptom, cause, fix. Exit codes first, because they tell you which half of the page you're
in.

| Exit code | Meaning |
| --- | --- |
| `0` | Patch coverage met the threshold, or there was nothing coverable to gate. |
| `1` | Patch coverage fell below the threshold. This is the gate doing its job. |
| `2` | Broken run, bad setup, or a usage error. **Never** a coverage verdict. |

Exit `2` is the one to read carefully. A failed, empty or unparseable test run is a tool
error, not 0% coverage, so it turns the build red instead of quietly failing the gate.

## The gate passed but the number looks too good

The most important entry on this page, because nothing fails and nothing is logged as an
error.

Patch coverage only counts changed lines the coverage report actually mentions. A file the
report never names contributes nothing, so anything that drops files from the report inflates
the result instead of failing it.

Check the denominator first. `Covered / changed executable lines` in the PR comment, or
`total_lines` in the outputs. If it's `0` or much smaller than the diff, one of these is
happening:

- **A report was dropped.** A multi-project .NET solution writes one report per test project,
  and a Maven reactor writes one per module. Count them: `find . -name coverage.cobertura.xml
  | wc -l`, or `find . -path '*/target/site/jacoco/jacoco.xml' | wc -l`.
- **The paths don't match.** Coverage paths are often absolute or rooted differently from
  `git diff` paths. Use `strip_prefix` to peel a known root.
- **A JaCoCo report was read as Cobertura.** Both use `.xml`. Brimyr picks by root element,
  but an explicit `coverage_file: report.xml:cobertura` overrides that and produces an empty
  report. Drop the format suffix or use `:jacoco`.
- **`exclude` is too broad.** A glob like `*Migrations*` uses `fnmatch`, where `*` crosses
  `/`, so it matches deeper than you might expect.

## `the coverage report(s) named no files at all`

The report parsed but describes nothing, so there is no coverage to gate on. That's a
broken report, not 0%, and it exits `2`.

The usual causes:

- **JVM:** a surefire `<argLine>` that overrides instead of appending `@{argLine}`, which
  detaches the JaCoCo agent while the build stays green.
- **.NET:** `dotnet test` ran without `--collect:"XPlat Code Coverage"`, or the
  `coverlet.collector` package isn't referenced by the test project.
- **Any:** `coverage_file` points at a report from a different run, or at a file the
  coverage tool wrote before it instrumented anything.

## `tests did not finish within Ns and were killed`

The suite hit `test_timeout` (default 3600 seconds) and was killed. This is a broken run,
exit `2`, not 0% coverage.

Raise it if the suite is genuinely that slow:

```yaml
        with:
          test_timeout: '7200'
```

Set `'0'` to wait indefinitely, which restores the old behaviour: a hung suite then holds
the runner until the job timeout, six hours by default on GitHub-hosted runners.

## `History is shallow, so the merge-base is unavailable.`

`actions/checkout` defaults to `fetch-depth: 1`, and patch coverage needs the merge base.

Set `fetch-depth: 0`:

```yaml
      - uses: actions/checkout@v6
        with: { fetch-depth: 0 }
```

If you let Brimyr do its own checkout, leave `fetch_depth` at its default of `0`. Locally,
run `git fetch --unshallow`.

## `No merge-base could be determined for the given base and head.`

The two refs share no history. Usually the base branch isn't fetched, or you're comparing
across unrelated histories such as an orphan branch.

Fetch the base, or pass `--no-merge-base` to diff the two refs directly without a common
ancestor.

## `No test suite detected`: the run passed but nothing was gated

Not an error. Auto-detection found no ecosystem marker, so no tests ran and there is
nothing to gate: the job is green, `gate_result` is `skipped`, and the summary says so
in place of the coverage table.

This is what makes Brimyr safe to provision across a whole org rather than adopt one
repo at a time. A charts, Terraform, prompts or docs repo has nothing to test, and
failing it would mean a permanently red check on a gate it can never satisfy.

**If the repo DOES have tests, this is the bug, not the fix.** The warning is loud on
purpose. Detection needs one of:

- `pyproject.toml` / `setup.py` / `setup.cfg` / `requirements.txt` / `tox.ini` **plus a
  real Python test signal**: a `test_*.py` or `*_test.py` file anywhere outside
  `.venv` / `node_modules` / other vendored directories, or a pytest section
  (`[tool.pytest.ini_options]`, `[pytest]`, `[tool:pytest]`).
- `package.json` **plus** a jest/vitest config or a non-placeholder `test` script.
- `pom.xml` for Java (`build.gradle` is recognised but not auto-run, see above).
- `*.sln` / `*.slnx` / `*.csproj` in the repo root for .NET.

Force it with `ecosystem:`, or skip detection entirely with `coverage_file:`. A forced
`ecosystem:` that then fails is still an error: explicit intent is never downgraded to
a skip.

## `the test command did not run: ... command not found`

Nothing on the runner could launch the test binary — most often `pytest` or `jest` on a
job that never installed the repo's dependencies. Nothing was measured, so this is a
broken run (exit 2), not 0% coverage.

Brimyr normally installs them for you: with `provision` on (the default) it uses the
repo's own dependency manager, so a `uv.lock` repo runs under `uv run` and a
`package.json` repo gets an `npm ci` first. The line above this error says why that
declined — the common ones:

| Line | Fix |
| --- | --- |
| ``` `uv` is not on PATH — running tests as-is ``` | The action installs `uv` for you when `provision` is `true`; you'll only see this outside the action, e.g. `brimyr local`. Install `uv`, or install your test dependencies. |
| `no installable Python project found` | No `pyproject.toml`, no `requirements*.txt`. Add one, or set `test_command`. |
| ``` poetry project, but `poetry` is not on PATH ``` | A pre-2.0 Poetry layout (`[tool.poetry]`, no `[project]`). Install Poetry in the job. |

You can always take it over yourself: install the dependencies in an earlier step and
set `provision: 'false'`, set `test_command` to something that works (which disables
provisioning too), or skip the test run with `coverage_file`. The full table is in the
[Action reference](action.md#dependency-provisioning).

## `dependency install failed`

The provisioning step itself exited non-zero — `npm ci` against a stale lockfile,
`poetry install` on an unresolvable graph, `uv run` on a lockfile that no longer matches
`pyproject.toml`. The tests were **not** run, so again this is a broken run, not a
coverage number. The failing command and its exit code are in the message; run it
locally to see the real error.

## The tests ran but the build is red with exit 2

That's the broken-run rule. The suite failed, produced no coverage file, or wrote something
unparseable. Look at the test output above the Brimyr step: the underlying failure is there,
and Brimyr is refusing to convert it into a coverage number.

## Small pull requests aren't being gated

Working as intended. `min_lines` defaults to `20`, so a diff with fewer changed executable
lines than that isn't gated, and the summary says so:

```text
⚪ Only 3 changed executable line(s), below the 20-line minimum, so the 80.0%
threshold was not applied (patch coverage was 33.3%).
```

Set `min_lines: '0'` to gate every diff. See [Patch coverage](patch-coverage.md).

## `skipped (sonar-scanner not found on PATH)`

The scanner install didn't happen or didn't succeed. It only runs when `sonar_url` is set,
and it's deliberately non-fatal, so the job stays green with no analysis uploaded.

Check that `sonar_url` is non-empty, and look for an earlier `::warning::` from the install
step. On a container or self-hosted runner without .NET, add `actions/setup-dotnet` before
Brimyr for .NET repos.

## `skipped (no token in $SONAR_TOKEN)`

`sonar_url` is set but `sonar_token` is empty. Non-blocking by design, so nothing failed.
Pass the secret, or clear `sonar_url` if you didn't mean to enable the leg.

## `skipped (Java / JVM needs sonar.java.binaries)`

`sonar-scanner` can't analyse Java without compiled classes. Supply them:

```yaml
          sonar_args: '-Dsonar.java.binaries=**/target/classes'
```

See [SonarQube](sonarqube.md#java-needs-sonarjavabinaries).

## No HTML report artifact appeared

`html_report` needs ReportGenerator, which needs the .NET runtime. On GitHub-hosted runners
the SDK is preinstalled. Elsewhere you'll see:

```text
::warning::html_report needs the .NET runtime, add actions/setup-dotnet
```

Add `actions/setup-dotnet` before the Brimyr step. The gate is unaffected either way.

## The PR comment isn't posted

The comment is failure-isolated, so it never fails the run. Check, in order:

1. `pr_comment: 'true'` is set.
2. The workflow has `pull-requests: write`.
3. The run is on a pull request. There's no PR to comment on for a push.
4. The event is `pull_request`, not `pull_request_target`.

Fork pull requests get a read-only token, so the comment can't post. That's a GitHub
restriction, not a Brimyr one.

## The comment says `github-actions[bot]` instead of `Brimyr[bot]`

The token broker didn't answer, and the run fell back to the job token. That fallback is
silent and deliberate: the comment still posts, only the byline changes. Check that
`token_broker_url` is set and the workflow has `id-token: write`.
