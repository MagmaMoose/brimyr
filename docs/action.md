# Action reference

<!-- sources: action.yml, src/brimyr/cli.py -->

Every input and output of the `magmamoose/brimyr` composite action, with the default the
code actually uses. Inputs are all optional. For the CLI behind it, see
[CLI reference](cli.md).

## Checkout

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `checkout` | bool | `true` | Run `actions/checkout` first. Set to `false` if you already checked out, which you must do if any earlier step touches the workspace. |
| `fetch_depth` | int | `0` | Checkout depth. Must stay `0`: patch coverage needs the merge base, and a shallow clone fails with `ShallowCloneError`. |

## Gate

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `mode` | enum | `auto` | `auto`, `pr` (patch-coverage gate) or `baseline` (report only, never blocks). `auto` picks `pr` on a pull request and `baseline` on a push. |
| `threshold` | float | `80` | Patch coverage below this percentage fails the gate with exit `1`. |
| `min_lines` | int | `20` | Diffs with fewer changed executable lines than this are not gated at all. Set `0` to gate every diff. See [Patch coverage](patch-coverage.md#the-sample-size-floor). |
| `base_ref` | string | *(PR base SHA)* | Override the base ref or SHA to diff against. |
| `head_ref` | string | *(PR head SHA, else `github.sha`)* | Override the head ref or SHA. |
| `exclude` | string | *(empty)* | Comma-separated globs. Matching changed files leave the denominator entirely, so they are not counted as covered, they just don't count. `*` crosses `/`. |
| `strip_prefix` | string | *(empty)* | Comma-separated path prefixes to strip from coverage paths before matching them to diff paths. |

## Test run

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `ecosystem` | string | *(auto-detect)* | Force one or more of `python`, `javascript`, `dotnet`, `java`, comma-separated. |
| `test_command` | string | *(detected)* | Replace the detected test command with a shell command string. |
| `coverage_file` | string | *(empty)* | Ingest pre-made reports as `path[:format]`, comma-separated, and skip the test run. Format is sniffed when you leave it off. |

## PR comment

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `pr_comment` | bool | `false` | Post one consolidated comment and update it in place on later pushes. Never blocks. |
| `github_token` | string | `${{ github.token }}` | Token used to comment. Needs `pull-requests: write`. The job token comments as `github-actions[bot]`. |
| `token_broker_url` | string | *(empty)* | Broker base URL. When set, the comment is authored by `Brimyr[bot]` instead. Fails soft: any problem falls back to the job token. See [PR comment](pr-comment.md). |

## SonarQube

Set `sonar_url` and `sonar_token` and the action installs a scanner and runs an analysis.
Everything here is non-blocking. See [SonarQube](sonarqube.md).

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `sonar_url` | string | *(empty)* | SonarQube host URL. Empty disables the whole Sonar leg. |
| `sonar_token` | string | *(empty)* | SonarQube token. Pass a secret. Used only when `sonar_url` is set. |
| `sonar_project_key` | string | *(repo slug)* | Project key. Defaults to `owner_repo`, because Sonar keys can't contain `/`. |
| `sonar_organization` | string | *(empty)* | Sonar organization. SonarCloud only. |
| `sonar_sources` | string | *(repo root)* | Value for `sonar.sources`. |
| `sonar_args` | string | *(empty)* | Extra properties, comma-separated, each as `-Dkey=value`. Java needs `-Dsonar.java.binaries=...` here or the analysis is skipped. |
| `sonar_scanner_version` | string | `8.1.0.6389` | SonarScanner CLI version installed for non-.NET repos. Digits and dots only. .NET repos get `dotnet-sonarscanner` instead. |

## Artifacts

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `html_report` | bool | `false` | Render a browsable HTML coverage report and upload it. Needs the .NET runtime for ReportGenerator. See [HTML report](html-report.md). |
| `html_artifact_name` | string | `brimyr-coverage-html` | Artifact name for that report. |
| `emit_json_artifact` | bool | `true` | Upload the patch-coverage JSON summary. |
| `json_artifact_name` | string | `brimyr-coverage` | Artifact name for the JSON summary. |

## Runtime

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `python_version` | string | `3.12` | Python used to run the CLI. Brimyr installs itself into its own venv and calls it by absolute path, so it doesn't depend on this. |

!!! warning "`python_version` has a job-wide side effect"
    The action runs `actions/setup-python`, which changes the Python for every step
    **after** it, not just for Brimyr. Leave it alone unless the runner's default Python
    is older than 3.11.

## Outputs

| Output | Description |
| --- | --- |
| `mode` | Resolved run mode: `pr` or `baseline`. |
| `gate_result` | `pass`, `fail` or `error`. `error` means a broken run, never 0% coverage. |
| `patch_coverage` | Patch coverage percentage, two decimal places. |
| `covered_lines` | Covered changed executable lines. |
| `total_lines` | Total changed executable lines, the patch-coverage denominator. |
| `total_coverage` | Overall coverage across the files the run measured. **Empty string** when nothing was measured, never `0.00`, so an unmeasured run and a genuinely zero-covered one don't look the same to a downstream `if`. |

## Permissions

```yaml
permissions:
  contents: read
  pull-requests: write   # only if pr_comment is true
  id-token: write        # only if token_broker_url is set
```

## Environment variables

The action sets these for you. They matter if you run the CLI directly.

| Variable | Read by | Purpose |
| --- | --- | --- |
| `SONAR_TOKEN` | `sonar.py`, `sonar_dotnet.py` | Sonar credential. Passed in the environment, never on argv, so it can't leak into a process listing. Rename with `--sonar-token-env`. |
| `GITHUB_TOKEN` | `github_comment.py` | Comment credential. Rename with `--github-token-env`. |
| `GITHUB_API_URL` | `cli.py` | API base. Set this for GitHub Enterprise. Defaults to `https://api.github.com`. |
| `GITHUB_REPOSITORY` | `cli.py` | `owner/repo`. Used for the comment target and the default Sonar project key. |
| `GITHUB_EVENT_NAME`, `GITHUB_EVENT_PATH` | `cli.py`, `modes.py` | Mode resolution and PR number discovery. |
| `GITHUB_OUTPUT`, `GITHUB_STEP_SUMMARY` | `report.py` | Where step outputs and the job summary are written. |
| `GITHUB_ACTIONS` | `cli.py` | Switches warnings to `::warning::` annotations. |
| `ACTIONS_ID_TOKEN_REQUEST_URL`, `ACTIONS_ID_TOKEN_REQUEST_TOKEN` | `broker_client.py` | Injected by Actions when `id-token: write` is set. Used to mint the OIDC token the broker exchanges. |
