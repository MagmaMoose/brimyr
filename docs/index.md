# Brimyr

**Point Brimyr at any repo and it figures out the rest.** It detects the ecosystem,
runs the test suite with coverage instrumentation on, and gates the pull request on
the coverage of the lines that pull request changed (diff-cover semantics) — blocking
below a threshold (default **80%**). Pre-existing uncovered code never blocks.

```yaml
      - uses: magmamoose/brimyr@v1     # that is the whole configuration
```

That is the part nothing else does. Patch-coverage *maths* is well-trodden; what
every other tool has in common is that **you bring your own coverage report** — you
work out the right test command for each language and wire it per repo. Across an
estate in several languages, that wiring *is* the project.

Brimyr is **quality assurance**; [Chargate](https://github.com/MagmaMoose/chargate)
is **security assurance**. Twins, not competitors.

The same run, non-blocking, drives `sonar-scanner` to ship quality + coverage to
SonarQube for the long-run trend. It is the coverage sibling of
[Chargate](https://github.com/MagmaMoose/chargate) (net-new security/lint) and
[Diatreme](https://github.com/MagmaMoose/diatreme) (build + release).

## Two faces, kept separate

- **Blocking — the patch-coverage gate.** The percentage of *changed executable
  lines* the tests covered, diffed against `merge-base(base, head)`. Blocks below
  the threshold. Computed **locally**; no SonarQube involvement.
- **Non-blocking — one `sonar-scanner` run.** Sonar's native quality analysis plus
  ingesting the coverage file → SonarQube, for history and the coverage/quality
  trend. Sonar derives new-vs-old code itself (its New Code Period); you never feed
  it "net-new".

!!! danger "Broken test run ≠ 0% coverage"
    If the test run failed, timed out, or produced no coverage, that is an **error
    (build red)** — never reported as "0% patch coverage" that hard-fails the gate.

## Coverage is a byproduct of the test run

You run the tests *with instrumentation on* (`pytest --cov`, `jest --coverage`,
`dotnet test --collect`) and that single run emits the coverage file. There is no
separate "measure coverage" pass. Brimyr detects the ecosystem and runs the right
command; polyglot repos produce **one coverage file per language**, merged.

## Two surfaces, one CLI

| Surface | What it is | When to use |
| --- | --- | --- |
| **Composite action** | `action.yml` | When you compose your own steps. |
| **pre-push hook** | `.pre-commit-hooks.yaml` (`brimyr` hook) | Catch a shortfall locally before pushing. |

See [Setup & usage](setup.md) to wire one up, [Architecture](architecture.md) for
how it fits together, and [Patch coverage](patch-coverage.md) for the precise
classification rules.

## The PR comment

Opt in with `pr_comment` and the verdict lands on the pull request as **one**
comment, updated in place on every push rather than stacked — the number, the
threshold, and the changed lines the tests never executed. Set `token_broker_url`
as well and it is authored by **Brimyr[bot]** rather than the shared
`github-actions[bot]`. Neither can fail the gate: a comment is a convenience, and a
convenience must never turn a green PR red. See [PR comment](pr-comment.md).

## Modes

- **PR events** → run tests → patch-coverage gate → ship to SonarQube.
- **Push to default branch / scheduled** → run tests → ship to SonarQube as the
  trend baseline → **no** gate.

`mode: auto` (default) picks this from the event; force it with `mode: pr|baseline`.

## License

MIT.
