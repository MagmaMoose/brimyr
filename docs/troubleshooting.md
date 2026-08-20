# Troubleshooting

<!-- sources: src/brimyr/git.py, src/brimyr/cli.py, src/brimyr/runner.py, src/brimyr/sonar.py, src/brimyr/html_report.py -->

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

## `no ecosystem detected`

The full message names all three fixes:

```text
no ecosystem detected, add a marker file, pass --ecosystem, or supply --coverage-file
to ingest a pre-made report.
```

Detection looks for `pyproject.toml`, `package.json`, `*.sln` / `*.csproj`, or `pom.xml` in
the repo root. Two cases surprise people:

- **A bare `package.json`** with no test script and no jest or vitest config is deliberately
  not detected, so Brimyr doesn't run `jest` against a repo that only ships frontend assets.
- **Gradle** is recognised as Java but not auto-detected, because the built-in command is
  `mvn`. Pass `ecosystem: 'java'` with your own `test_command`.

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
