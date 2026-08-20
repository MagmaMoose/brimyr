# Setup & usage

Brimyr runs your tests **on the runner**, so the test toolchain and dependencies
must be present before the gate runs. Install them in a `setup` step (reusable
workflow) or your own steps (composite action) — or skip the run entirely by
feeding a pre-made coverage report via `coverage_file`.

## 1. Reusable workflow (recommended)

```yaml
# .github/workflows/coverage.yml
name: Coverage
on:
  pull_request:
  push:
    branches: [main]

permissions:
  contents: read
  pull-requests: write   # for the PR comment

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - run: pip install -e '.[test]'          # install your test deps first
      - uses: magmamoose/brimyr@v1
        with:
          threshold: '80'
          pr_comment: 'true'
          # sonar_url: https://sonar.example.com
          # sonar_token: ${{ secrets.SONAR_TOKEN }}
```

On PRs it runs your tests with coverage, gates on patch coverage, and (if
`sonar_url` is set) ships to SonarQube. On push to the default branch it runs a
non-gating baseline that still feeds the trend.

> **Wire it on `pull_request`, not `pull_request_target`.** Brimyr runs the PR's
> *own* test code on the runner. `pull_request_target` runs that code with the base
> repo's write token and secrets in scope, so a malicious fork could exfiltrate
> them — exactly the privilege a coverage gate doesn't need. Both events gate if
> Brimyr sees them, but `pull_request` is the safe default; only reach for
> `pull_request_target` if you fully control who can open PRs.

## 2. Composite action

```yaml
name: Coverage
on: [pull_request]

permissions:
  contents: read
  pull-requests: read

jobs:
  brimyr:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/setup-python@v6
        with: { python-version: '3.12' }
      - run: pip install -e '.[test]'          # your test deps
      - uses: magmamoose/brimyr@v1
        with:
          checkout: 'false'                     # you already checked out
          threshold: '85'
          # sonar_url: https://sonar.example.com
          # sonar_token: ${{ secrets.SONAR_TOKEN }}
```

The action checks out with `fetch-depth: 0` by default (patch coverage needs the
merge-base). Set `checkout: 'false'` if you already checked out with full history.

## 3. pre-push hook

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

Brimyr runs `sonar-scanner` after the gate, pointing it at the coverage file(s) it
produced. **A Sonar failure never fails the gate** — it is logged and the run
continues. The token is passed via `SONAR_TOKEN`, never on the command line.

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

Brimyr can post **one** consolidated patch-coverage comment on the pull request —
the percentage, the threshold, and the changed lines the tests never executed. It
is updated in place on every push rather than stacked, so a long-running PR keeps
exactly one comment.

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

Add `token_broker_url` — plus `id-token: write` on the job — and the comment is
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
SonarQube step follows. The broker is non-blocking too — and *silently* so: any
problem with it costs the byline and nothing else.

Both `pr_comment` and `token_broker_url` are inputs of the **composite action**;
the reusable workflow does not forward them today. See
[PR comment](pr-comment.md) for what the comment looks like, how the
single-comment marker works, and the full list of broker failure messages.
