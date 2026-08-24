# Setup & usage

<!-- sources: action.yml, .pre-commit-hooks.yaml -->

Brimyr gates a pull request on two things: **patch coverage** (always) and **net-new
quality findings** (opt-in). It runs your tests **on the runner**, so the test toolchain
and dependencies must be present before the gate runs. Install them in a `setup` step (reusable
workflow) or your own steps (composite action), or skip the run entirely by
feeding a pre-made coverage report via `coverage_file`.

## 1. Composite action

```yaml
# .github/workflows/coverage.yml
name: Coverage
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write   # only needed for the PR comment

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      # Check out FIRST: the deps install below needs a populated workspace, so
      # the action's own checkout would be too late.
      - uses: actions/checkout@v6
        with: { fetch-depth: 0 }
      - run: pip install -e '.[test]'          # install your test deps first
      - uses: magmamoose/brimyr@v1
        with:
          checkout: 'false'                     # already checked out above
          threshold: '80'
          pr_comment: 'true'
          # sonar_url: https://sonar.example.com
          # sonar_token: ${{ secrets.SONAR_TOKEN }}
```

On pull requests it runs your tests with coverage, gates on patch coverage, and ships
to SonarQube when `sonar_url` is set. On a push to the default branch it runs a
non-gating baseline that still feeds the trend.

If Brimyr is your only step, drop both the checkout and `checkout: 'false'`: the action
checks out with `fetch-depth: 0` by default, which is what patch coverage needs. Any
step that touches the workspace first needs the explicit checkout, because the action's
own would run too late.

Every input and output is in the [Action reference](action.md).

> **Wire it on `pull_request`, not `pull_request_target`.** Brimyr runs the PR's
> *own* test code on the runner. `pull_request_target` runs that code with the base
> repo's write token and secrets in scope, so a malicious fork could exfiltrate
> them, exactly the privilege a gate that runs the PR's tests doesn't need. Both
> events gate if
> Brimyr sees them, but `pull_request` is the safe default; only reach for
> `pull_request_target` if you fully control who can open PRs.

## 2. pre-push hook

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/MagmaMoose/brimyr
    rev: v1.0.0
    hooks:
      - id: brimyr
```

```sh
pre-commit install --hook-type pre-push
git push     # runs the tests + patch-coverage gate against the default branch
```

It runs at **pre-push** (not pre-commit): patch coverage means running the test
suite, too heavy for every commit but fine before a push.

## Ingesting a pre-made report (escape hatch)

If your CI already produces a coverage file, point Brimyr at it instead of letting
it run the tests:

```yaml
- uses: magmamoose/brimyr@v1
  with:
    coverage_file: coverage.xml          # or coverage/lcov.info, comma-separated
    checkout: 'false'
```

Format is inferred from the extension (`.xml` → Cobertura, `.info`/`.lcov` → lcov);
append `:lcov` / `:cobertura` to force it.

## SonarQube

Optional and non-blocking:

```yaml
- uses: magmamoose/brimyr@v1
  with:
    sonar_url: https://sonarqube.example.com
    sonar_token: ${{ secrets.SONAR_TOKEN }}
    sonar_project_key: my-service
```

The action installs a scanner for you and points it at the coverage files it just
produced. **A Sonar failure never fails the gate**: it becomes a `::warning::` and the
run continues. The token travels in `SONAR_TOKEN`, never on the command line.

.NET is the exception, and Brimyr handles it automatically: the CLI scanner can't
analyse C# at all, so `dotnet sonarscanner` wraps the build instead of following it.
Java needs `sonar_args: '-Dsonar.java.binaries=...'` or the analysis is skipped with a
warning. Both are covered in [SonarQube](sonarqube.md).

## Quality findings

Off by default. Turn it on and the action runs
[Chargate](https://github.com/MagmaMoose/chargate) as a nested step and folds the
net-new findings into the same summary and the same comment as coverage:

```yaml
- uses: magmamoose/brimyr@v1
  with:
    quality: 'true'
    # quality_fail_on: 'error'   # leave unset to stay report-only
```

It needs **Docker** on the runner. `quality_fail_on` defaults to `none`, so the half
ships **report-only**: findings are counted and shown, nothing blocks. Measure a release
cycle before picking a level — the failure mode here is abandonment, not error.

The threshold speaks SARIF levels (`note`, `warning`, `error`, `any`), not Chargate's
severity bands. Full detail, and why, in [Quality findings](quality-findings.md).

## Local development

```sh
uv sync                       # install deps + dev tools
uv run pytest -q              # run the test suite
uv run ruff check .          # lint
uv run ruff format --check . # format check (CI gate)
```

(If `uv` is not on PATH, `python -m uv ...` works after `pip install uv`.)

## Building these docs

```sh
uv run --group docs mkdocs serve   # live preview at http://127.0.0.1:8000
uv run --group docs mkdocs build   # render to ./site (gitignored)
```

The `docs` dependency group (`mkdocs-material`) is non-default, so `uv sync` and CI
are unaffected until you opt in with `--group docs`.

## PR comment

Brimyr can post **one** consolidated comment on the pull request. It carries the
coverage verdict — the percentage, the threshold, and the changed lines the tests never
executed — and, when the quality half is on, the net-new findings beneath it, under a
second heading in the same comment. It is updated in place on every push rather than
stacked, so a long-running PR keeps exactly one comment.

```yaml
permissions:
  contents: read
  pull-requests: write     # ← required; the default `read` cannot comment

# ...
      - uses: magmamoose/brimyr@v1
        with:
          pr_comment: 'true'
          # github_token defaults to the job token (comments as github-actions[bot])
```

Add `token_broker_url`, plus `id-token: write` on the job, and the comment is
authored by **Brimyr[bot]** instead of the shared `github-actions[bot]`:

```yaml
permissions:
  contents: read
  pull-requests: write
  id-token: write          # ← required to mint the Brimyr[bot] identity

# ...
        with:
          pr_comment: 'true'
          token_broker_url: https://broker-brimyr.magmamoose.com
```

Commenting is **non-blocking**: a missing token, a 403, or a network failure is
reported on stderr and never changes the gate verdict, the same contract the
SonarQube step follows. The broker is non-blocking too, and *silently* so: any
problem with it costs the byline and nothing else.

Both `pr_comment` and `token_broker_url` are inputs of the **composite action**;
the reusable workflow does not forward them today. See
[PR comment](pr-comment.md) for what both blocks look like, how the two
comment markers work, and the full list of broker failure messages.
