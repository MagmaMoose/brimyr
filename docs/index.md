# Brimyr

<!-- sources: README.md, src/brimyr/cli.py, src/brimyr/quality.py, action.yml -->

**Point Brimyr at any repo and it figures out the rest.** It detects the ecosystem,
runs the test suite with coverage instrumentation on, and gates the pull request on
the coverage of the lines that pull request changed (diff-cover semantics), blocking
below a threshold (default **80%**). Pre-existing uncovered code never blocks.

```yaml
      - uses: magmamoose/brimyr@v1     # that is the whole configuration
```

That is the part nothing else does. Patch-coverage *maths* is well-trodden; what
every other tool has in common is that **you bring your own coverage report**: you
work out the right test command for each language and wire it per repo. Across an
estate in several languages, that wiring *is* the project.

Brimyr is **quality assurance**; [Chargate](https://github.com/MagmaMoose/chargate)
is **security assurance**, and [Diatreme](https://github.com/MagmaMoose/diatreme)
builds and releases. Twins, not competitors — though coverage is only half of quality
assurance, so Brimyr runs Chargate's *quality* linters for the other half rather than
growing its own ([see below](#coverage-is-half-of-quality-assurance)).

## What it detects

| Ecosystem | Markers | Test command | Coverage format |
| --- | --- | --- | --- |
| Python | `pyproject.toml`, `setup.py`, `tox.ini` | `pytest --cov` | Cobertura |
| JavaScript / TypeScript | `package.json` | `jest`, or `vitest run` when the repo uses vitest | lcov |
| .NET | `*.sln`, `*.csproj`, `*.fsproj`, `*.vbproj` | `dotnet test --collect` | Cobertura |
| Java / JVM | `pom.xml` | `mvn ... jacoco:prepare-agent test jacoco:report` | JaCoCo |

A polyglot repo matches more than one, produces one report per language, and Brimyr
merges them into a single number. Override any of it with `ecosystem`,
`test_command`, or `coverage_file`. See [Action reference](action.md).

The same run, non-blocking, ships coverage to SonarQube for the long-run trend. See
[SonarQube](sonarqube.md).

## Two faces, kept separate

- **Blocking: the patch-coverage gate.** The percentage of *changed executable
  lines* the tests covered, diffed against `merge-base(base, head)`. Blocks below
  the threshold. Computed **locally**; no SonarQube involvement.
- **Non-blocking: one `sonar-scanner` run.** Sonar's native quality analysis plus
  ingesting the coverage file → SonarQube, for history and the coverage/quality
  trend. Sonar derives new-vs-old code itself (its New Code Period); you never feed
  it "net-new".

!!! danger "Broken test run ≠ 0% coverage"
    If the test run failed, timed out, or produced no coverage, that is an **error
    (build red)**: never reported as "0% patch coverage" that hard-fails the gate.

## Coverage is a byproduct of the test run

You run the tests *with instrumentation on* (`pytest --cov`, `jest --coverage`,
`dotnet test --collect`) and that single run emits the coverage file. There is no
separate "measure coverage" pass. Brimyr detects the ecosystem and runs the right
command; polyglot repos produce **one coverage file per language**, merged.

## Coverage is half of quality assurance

The other half is what the linters say. Set `quality: 'true'` and Brimyr also gates the
**net-new quality findings** this pull request introduced, classified against the same
diff the coverage gate uses, and folds that verdict into the same job summary and the
same PR comment as coverage. The job exits on the worse of the two.

Brimyr implements none of that classification. Chargate already owns a finished net-new
engine, so Brimyr **calls it as a nested step** rather than sharing a library: a shared
package would buy version skew, lockfile drift and a diamond dependency inside one job,
and a subprocess in its own environment has none of those properties. Chargate's own
`fail_on` is pinned to `none` there, so it can never set the job's exit code — it
reports, Brimyr decides.

`quality_fail_on` defaults to `none`, which makes it **report-only** until you say
otherwise. MegaLinter's quality half over a mature repo is far denser than its security
half, and a first PR that goes red with hundreds of findings is how a gate becomes
decoration nobody reads. Ship it reporting, measure a release cycle, then pick a SARIF
level. See [Quality findings](quality-findings.md).

!!! warning "The pinned Chargate release does not ship the quality flavor yet"
    `action.yml` pins Chargate at `v2.11.25`, which has no `quality` flavor, so
    `quality: 'true'` cannot work until Chargate releases it and that pin is bumped.
    Until then the nested step leaves no counts file behind and the run exits **2**:
    a gate that cannot evaluate its input does not go green.

## Two surfaces, one CLI

| Surface | What it is | When to use |
| --- | --- | --- |
| **Composite action** | `action.yml` | When you compose your own steps. |
| **pre-push hook** | `.pre-commit-hooks.yaml` (`brimyr` hook) | Catch a shortfall locally before pushing. |

See [Setup & usage](setup.md) to wire one up, [Action reference](action.md) for every
input and output, [Architecture](architecture.md) for how it fits together,
[Patch coverage](patch-coverage.md) for the precise classification rules, and
[Quality findings](quality-findings.md) for the other half. When something goes wrong,
[Troubleshooting](troubleshooting.md).

## The PR comment

Opt in with `pr_comment` and the verdict lands on the pull request as **one**
comment, updated in place on every push rather than stacked: the number, the
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
