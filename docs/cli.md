# CLI reference

<!-- sources: src/brimyr/cli.py, src/brimyr/gate.py, src/brimyr/quality.py -->

All three GitHub surfaces drive the same `brimyr` CLI. Exit codes: `0` pass · `1`
patch coverage below threshold, or blocking net-new quality findings · `2` broken test
run / setup / usage error, a quality input the gate could not evaluate, or a quality
scan that did not complete. A run that does both halves exits with the worse of the two.

```sh
brimyr <coverage | ci | local | lint | version> [options]
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
| `--coverage-file` | (required) | Coverage file `path[:format]` (`lcov`\|`cobertura`\|`jacoco`; inferred from the extension, and `.xml` is resolved by its root element). Repeatable. |
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
(non-blocking). Given the [quality flags](#quality-flags) it decides the net-new
quality half as well, and renders both verdicts into one summary and one comment.

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

Also accepted by `brimyr local` and `brimyr lint`, though they only do anything when
there is a PR to comment on and a token to comment with. All of them are non-blocking:
nothing here can change the exit code. See [PR comment](pr-comment.md) for the full
behaviour.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--pr-comment` | off | Post/update the single comment on the PR — consolidated (coverage plus the quality block, when that half ran) on `ci` and `local`; quality only on `lint`, under its own marker. |
| `--pr-number` | from the event | PR number (read from `$GITHUB_EVENT_PATH` otherwise). |
| `--repo-slug` | `$GITHUB_REPOSITORY` | `owner/repo` being commented on. |
| `--github-token-env` | `GITHUB_TOKEN` | Env var holding the comment token; needs `pull-requests: write`. |
| `--token-broker-url` | none | Broker base URL. Authors as `Brimyr[bot]`; falls back **silently** to the token above on any failure. Needs `id-token: write`. |
| `--github-api-url` | `$GITHUB_API_URL`, else `api.github.com` | GitHub API base (GitHub Enterprise). |

### Quality flags

Also accepted by `brimyr local`. Supplying **either** `--quality-counts` or
`--quality-scan-broken` turns the quality half on: Brimyr reads what `chargate
filter-sarif` left behind — or, for a scan that did not complete, reads nothing and says
so — decides pass/fail on its own threshold, and renders that verdict into the **same**
summary and the **same** PR comment as coverage. One consolidated view is the reason to
prefer this over the standalone [`brimyr lint`](#brimyr-lint). The process exit code
becomes the worse of the two halves.

| Flag | Default | Purpose |
| --- | --- | --- |
| `--quality-counts` | none | Chargate's `filter-sarif --counts-json` output. The verdict's only input; supplying it turns the quality half on. |
| `--quality-findings` | none | Chargate's `filter-sarif --out` net-new SARIF. Read only to list findings in the summary; a result count that contradicts the counts JSON is a hard error (exit `2`). |
| `--quality-fail-on` | `none` | SARIF level at or above which a net-new finding blocks: `none` (report only), `note`, `warning`, `error`, `any`. |
| `--quality-scan-broken` | off | The scan did not complete. Turns the quality half on, reads no file at all, and reports a tool error (exit `2`, `quality_gate_result` `error`) — the counts file a failed scan leaves behind is a row of zeros, which is what a clean PR looks like. |
| `--quality-scan-note` | none | Linters the scan could not run, stated in the summary next to the count. A completed scan is not necessarily a full one. Never blocks. |
| `--quality-json-out` | none | Write the quality summary as JSON here. |

## `brimyr local`

Run the gate against a **locally inferred** base (the repo's default branch) to check
a branch before pushing. Same flags as `ci` — including the quality and PR-comment
ones — plus an optional `--base` to override the inferred base. In practice it is the
coverage half you run locally, since the quality half needs Chargate's output.

```sh
brimyr local                 # detect, run tests, gate vs the default branch
brimyr local --base main
```

## `brimyr lint`

Gate on the net-new **quality** findings Chargate already classified. Brimyr does not
import, vendor or re-implement that engine — it reads the two files `chargate
filter-sarif` writes and decides on them. Chargate reports, Brimyr gates. Runs no linter
and parses no diff.

```sh
brimyr lint --counts chargate-reports/counts.json \
    --findings chargate-reports/net-new.sarif --fail-on error
```

| Flag | Default | Purpose |
| --- | --- | --- |
| `--counts` | none | Chargate's `filter-sarif --counts-json` output. The gate's only input. Required unless `--scan-broken` says there is nothing worth reading. |
| `--findings` | none | Chargate's `filter-sarif --out` net-new SARIF. Read only to list findings in the summary; a result count that contradicts `--counts` is a hard error (exit `2`). |
| `--fail-on` | `none` | SARIF level at or above which a net-new finding blocks: `none` (report only), `note`, `warning`, `error`, `any`. |
| `--no-gate` | off | Always exit `0` (report only). |
| `--scan-note` | none | Linters the scan could not run, stated in the summary next to the count. A completed scan is not necessarily a full one. Never blocks. |
| `--scan-broken` | off | The quality scan did not complete. Skips every read and reports a tool error (exit `2`) — the counts file a failed scan leaves behind is a row of zeros, which is what a clean PR looks like. |
| `--json-out` | none | Write the quality summary as JSON here. |
| `--quiet` | off | Suppress the human summary. |

The threshold speaks SARIF **levels** and not Chargate's severity bands. Chargate gates
on per-result verdicts, where a missing `security-severity` falls back to the level, so
its bands work. Brimyr reads only the counts document, whose `per_severity_*` maps are
populated solely from a real `security-severity` — a property quality linters essentially
never emit — so a band-valued threshold read off it would match nothing on every PR.
`error` is the equivalent of Chargate's `fail_on: high`, and `any` blocks on every
net-new finding including the ones its linter left unlevelled.

It also takes the [PR comment flags](#pr-comment-flags), and comments under its **own**
marker: the two subcommands can run in either order, or only one of them, so sharing a
marker would mean whichever ran last erased the other's verdict. Two comments is the cost
of running the halves separately.

Exit codes are the coverage gate's: `0` pass · `1` blocking net-new findings · `2` an
input it could not read, a `schema_version` it does not recognise, counts that contradict
themselves, or `--scan-broken`. A gate that cannot evaluate its input never goes green.

## `brimyr version`

Prints the brimyr version (also `brimyr --version`).
