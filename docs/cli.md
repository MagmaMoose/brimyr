# CLI reference

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
| `--strip-prefix` | — | Path prefix to strip from coverage paths before matching (repeatable). |
| `--no-merge-base` | off | Diff `base..head` directly instead of `merge-base(base, head)..head`. |
| `--json-out` | — | Write the patch-coverage summary as JSON. |
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
| `--coverage-file` | — | Escape hatch: ingest a pre-made report instead of running tests. Repeatable. |
| `--ecosystem` | — | Force `python`\|`javascript`\|`dotnet` instead of auto-detect. Repeatable. |
| `--test-command` | — | Override the detected test command (a shell command string). |
| `--sonar-url` | — | SonarQube host URL (enables the non-blocking scanner run). |
| `--sonar-token-env` | `SONAR_TOKEN` | Env var holding the Sonar token. |
| `--sonar-project-key` / `--sonar-organization` | — | Sonar targeting. |
| `--sonar-sources` | `.` | `sonar.sources` value. |
| `--sonar-arg` | — | Extra raw `sonar-scanner` arg, e.g. `-Dsonar.foo=bar` (repeatable). |

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
