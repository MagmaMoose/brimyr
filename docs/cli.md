# CLI reference

<!-- sources: src/brimyr/cli.py, src/brimyr/gate.py -->

All three GitHub surfaces drive the same `brimyr` CLI. Exit codes: `0` pass · `1`
patch coverage below threshold · `2` broken test run / setup / usage error.

```sh
brimyr <coverage | ci | local | version> [options]
```

## `brimyr coverage`

The pure patch-coverage computation: coverage file(s) + a base/head → patch
coverage % + a gate exit code. Runs **no** tests, so it's decoupled from any
toolchain and unit-tested in isolation.

```sh
brimyr coverage --coverage-file coverage.xml --base "$BASE" \
    --threshold 80 --json-out coverage.json
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--coverage-file` | (required) | Coverage file `path[:format]` (`lcov`\|`cobertura`, inferred from ext). Repeatable. |
| `--base` | (required) | Base ref/SHA (PR target). |
| `--head` | `HEAD` | Head ref/SHA. |
| `--repo` | `.` | Path to the git repository. |
| `--threshold` | `80` | Patch-coverage % that blocks below it. |
| `--strip-prefix` | none | Path prefix to strip from coverage paths before matching (repeatable). |
| `--min-lines` | `20` | Skip the threshold when the diff has fewer changed executable lines than this. `0` gates every diff. |
| `--exclude` | none | Glob whose matching changed files leave the denominator entirely (repeatable). `*` crosses `/`. |
| `--no-merge-base` | off | Diff `base..head` directly instead of `merge-base(base, head)..head`. |
| `--json-out` | none | Write the patch-coverage summary as JSON. |
| `--no-gate` | off | Always exit `0` (report only). |
| `--quiet` | off | Suppress the human summary. |

## `brimyr ci`

The full CI flow: detect the ecosystem(s), run the right test command with
coverage, gate on patch coverage (PR events), and run `sonar-scanner`
(non-blocking).

```sh
brimyr ci --mode auto --sonar-url https://sonar.example.com --sonar-project-key my-svc
```

Key flags beyond the shared options:

| Flag | Default | Purpose |
| --- | --- | --- |
| `--mode` | `auto` | `auto` (from `GITHUB_EVENT_NAME`), `pr` (gate), or `baseline` (no gate). |
| `--coverage-file` | none | Escape hatch: ingest a pre-made report instead of running tests. Repeatable. |
| `--ecosystem` | none | Force `python`\|`javascript`\|`dotnet`\|`java` instead of auto-detect. Repeatable. |
| `--test-command` | none | Override the detected test command (a shell command string). |
| `--sonar-url` | none | SonarQube host URL (enables the non-blocking scanner run). |
| `--sonar-token-env` | `SONAR_TOKEN` | Env var holding the Sonar token. |
| `--sonar-project-key` / `--sonar-organization` | none | Sonar targeting. |
| `--sonar-sources` | `.` | `sonar.sources` value. |
| `--sonar-arg` | none | Extra raw `sonar-scanner` arg, e.g. `-Dsonar.foo=bar` (repeatable). |
| `--html-report` | none | Directory for a browsable HTML coverage report. Needs ReportGenerator; never affects the gate. |

### PR comment flags

Also accepted by `brimyr local`, though they only do anything when there is a PR to
comment on and a token to comment with. All of them are non-blocking: nothing here
can change the exit code. See [PR comment](pr-comment.md) for the full behaviour.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--pr-comment` | off | Post/update the single patch-coverage comment on the PR. |
| `--pr-number` | from the event | PR number (read from `$GITHUB_EVENT_PATH` otherwise). |
| `--repo-slug` | `$GITHUB_REPOSITORY` | `owner/repo` being commented on. |
| `--github-token-env` | `GITHUB_TOKEN` | Env var holding the comment token; needs `pull-requests: write`. |
| `--token-broker-url` | none | Broker base URL. Authors as `Brimyr[bot]`; falls back **silently** to the token above on any failure. Needs `id-token: write`. |
| `--github-api-url` | `$GITHUB_API_URL`, else `api.github.com` | GitHub API base (GitHub Enterprise). |

## `brimyr local`

Run the patch-coverage gate against a **locally inferred** base (the repo's default
branch) to check a branch before pushing. Same flags as `ci`, plus an optional
`--base` to override the inferred base.

```sh
brimyr local                 # detect, run tests, gate vs the default branch
brimyr local --base main
```

## `brimyr version`

Prints the brimyr version (also `brimyr --version`).
