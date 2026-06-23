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

jobs:
  brimyr:
    uses: magmamoose/brimyr/.github/workflows/gate.yml@v1
    with:
      setup: pip install -e '.[test]'        # install your test deps first
      threshold: '80'
    secrets:
      sonar_token: ${{ secrets.SONAR_TOKEN }} # optional
```

On PRs it runs your tests with coverage, gates on patch coverage, and (if
`sonar_url` is set) ships to SonarQube. On push to the default branch it runs a
non-gating baseline that still feeds the trend. Reusable workflows are consumed by
path, independent of the Marketplace listing.

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
