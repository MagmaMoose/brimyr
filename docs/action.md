# Action reference

<!-- sources: action.yml, src/brimyr/cli.py, src/brimyr/quality.py -->

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

## Quality findings

Coverage is half of quality assurance. Set `quality: true` and the action runs
[Chargate](https://github.com/MagmaMoose/chargate) as a nested step — MegaLinter's
quality linters → SARIF → net-new classification against this PR's diff — then
decides pass/fail on `quality_fail_on` and folds the verdict into the same job summary
and the same PR comment as coverage. Chargate reports, Brimyr gates. Needs Docker on
the runner.

| Input | Type | Default | Description |
| --- | --- | --- | --- |
| `quality` | bool | `false` | Also gate on net-new quality findings. Report-only even when on, until you set `quality_fail_on`. |
| `quality_fail_on` | enum | `none` | SARIF level at or above which a net-new finding blocks: `none` (report only), `note`, `warning`, `error`, `any`. `error` is the equivalent of Chargate's `fail_on: high`; `any` blocks on every net-new finding, including ones its linter left unlevelled. |
| `quality_linters` | string | *(empty)* | Comma-separated MegaLinter linter keys to run instead of Chargate's curated `quality` set (`GO_GOLANGCI_LINT`, `JAVASCRIPT_ES`, `JAVA_PMD`, `PYTHON_RUFF`, `TYPESCRIPT_ES`). A linter that emits no SARIF is invisible to the gate, and Chargate skips it by name with a reason rather than running it for nothing. |
| `quality_json_artifact_name` | string | `brimyr-quality` | Artifact name for the quality JSON summary. Uploaded when `quality` is on and `emit_json_artifact` is `true`. |

The threshold speaks SARIF **levels** and not Chargate's severity bands. Chargate gates
on per-result verdicts, where a missing `security-severity` falls back to the level, so
its bands work. Brimyr reads only the counts document, whose `per_severity_*` maps are
populated solely from a real `security-severity` — a property quality linters essentially
never emit — so a band-valued threshold read off it would match nothing on every PR. It
defaults to `none` for the same reason a first PR should not go red with hundreds of
findings: measure a release cycle, then pick a level.

The nested step is `magmamoose/chargate`, pinned by SHA and bumped by Dependabot like
every other action here. It runs with `fail_on: none` and `continue-on-error: true`, so
Chargate never sets this job's exit code and a Docker or MegaLinter failure can't take
the coverage gate down with it. That is not a silent pass: the action branches on that
step's `outcome`, and on anything other than `success` it passes `--quality-scan-broken`,
which reads no file at all and reports a scan that did not complete — exit `2`, and
`quality_gate_result` `error`.

It goes by the outcome rather than by the file because the file settles nothing.
`chargate ci` writes its counts JSON *before* it decides whether the scan produced any
runs, so a failed scan can leave a well-formed row of zeros behind, which is precisely
what a clean pull request looks like.

The verdict is folded into the coverage summary and the coverage comment, and the job's
exit code becomes the worse of the two halves on the `0` < `1` < `2` scale — clean
coverage does not launder a blocking quality finding. In `baseline` mode neither half
gates.

!!! note "An exit-`0` scan is not proof of a complete one"
    Chargate can decline to start a linter — no image for the runner's architecture, no
    SARIF output, the linter disabled — and still exit `0`, so anything that linter would
    have reported is missing from the count, and missing findings are what a clean repo
    looks like too. The action forwards Chargate's `linters_skipped` output as
    `--quality-scan-note`, and Brimyr states the shortfall next to the count in the
    summary and the comment. It never blocks. See
    [Quality findings](quality-findings.md#a-scan-that-completed-is-not-necessarily-a-full-one).

!!! warning "`quality: true` needs a Chargate release that does not exist yet"
    The pinned ref is `v2.11.25`, which has no `quality` flavor, so the nested step fails
    on it and Brimyr reports a broken scan: exit `2`, `quality_gate_result` `error`.
    Leave `quality` at `false` until Chargate ships the flavor and the pin here is bumped
    to that release.

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
| `quality_gate_result` | `pass`, `fail` or `error` for the quality half. `error` means the scan did not complete, which is the state to notice first: it is a tool error, not zero findings. Empty when `quality` is off. |
| `quality_net_new_count` | Net-new (PR-introduced) quality findings. Empty when `quality` is off. |
| `quality_blocking_count` | Net-new quality findings at or above `quality_fail_on` — the ones that actually block. |
| `quality_fail_on` | The threshold that was in force. It ships alongside the verdict because at `none` a report-only run and a genuinely clean one both say `pass`, and they are not the same thing. Empty when `quality` is off. |

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
