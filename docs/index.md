# Brimyr

<!-- sources: README.md, src/brimyr/cli.py, src/brimyr/quality.py, action.yml -->

**Brimyr is quality assurance for a pull request: it gates on the coverage of the
lines that PR changed, and on the net-new quality findings that PR introduced.** Point
it at any repo and it figures out the rest, it detects the ecosystem, runs the test
suite with coverage instrumentation on, and gates on the coverage of the changed lines
(diff-cover semantics), blocking below a threshold (default **80%**). Pre-existing
uncovered code never blocks. Turn `quality: 'true'` on and the same run also classifies
the linters' findings against that same diff. One job, one summary, one PR comment.

```yaml
      - uses: magmamoose/brimyr@v1     # that is the whole configuration
```

That is the part nothing else does. Patch-coverage *maths* is well-trodden; what
every other tool has in common is that **you bring your own coverage report**: you
work out the right test command for each language and wire it per repo. Across an
estate in several languages, that wiring *is* the project.

Brimyr is **quality assurance**; [Chargate](https://github.com/MagmaMoose/chargate)
is **security assurance**, and [Diatreme](https://github.com/MagmaMoose/diatreme)
builds and releases. Brimyr and Chargate are twins, not competitors, Chargate gates
net-new *security* findings, Brimyr gates coverage **and** net-new *quality*
findings. The line between them is the **subject**, not the tool: Brimyr's quality
half runs Chargate's net-new engine rather than growing its own
([see below](#coverage-is-half-of-quality-assurance)).

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

The same run can also ship coverage to SonarQube for the long-run trend, non-blocking. It does
that only when `sonar_url` and `sonar_token` are both set and a `sonar-scanner` is on PATH;
otherwise the step is skipped with a reason and nothing fails. See [SonarQube](sonarqube.md).

## Three faces, kept separate

- **Blocking: the patch-coverage gate.** The percentage of *changed executable
  lines* the tests covered, diffed against `merge-base(base, head)`. Blocks below
  the threshold. Computed **locally**; no SonarQube involvement.
- **Report-only by default: the net-new quality gate.** The quality findings *this
  PR introduced*, classified against that same diff. Off unless `quality: 'true'`,
  and even then `quality_fail_on` defaults to `none`, it counts and reports, and
  blocks only at a SARIF level you choose.
- **Non-blocking, and opt-in: one `sonar-scanner` run.** Off unless `sonar_url` and
  `sonar_token` are set. Sonar's native quality analysis plus
  ingesting the coverage file → SonarQube, for history and the coverage/quality
  trend. Sonar derives new-vs-old code itself (its New Code Period); you never feed
  it "net-new".

The two gates share one job, one summary and one PR comment, and the job exits on
the worse of the two.

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
engine, so Brimyr does not import it, vendor it or re-implement it, it **calls it as a
nested step**: a shared package would buy version skew, lockfile drift and a diamond
dependency inside one job, and a subprocess in its own environment has none of those
properties. That is
[ADR 0002](https://github.com/MagmaMoose/brimyr/blob/main/.claude/decisions/0002-quality-gate-calls-chargate.md).
Chargate's own `fail_on` is pinned to `none` there, so it can never set the job's exit
code, it reports, Brimyr decides.

`quality_fail_on` defaults to `none`, which makes it **report-only** until you say
otherwise. MegaLinter's quality half over a mature repo is far denser than its security
half, and a first PR that goes red with hundreds of findings is how a gate becomes
decoration nobody reads. Ship it reporting, measure a release cycle, then pick a SARIF
level. See [Quality findings](quality-findings.md).

A scan that never ran is not a clean one. The nested step is `continue-on-error`, so it
cannot take the coverage gate down with it, but Brimyr goes by that step's `outcome`
rather than by the counts file it may have left behind, a failed scan can leave a
well-formed row of zeros on disk, which is what a clean PR looks like, and reports a
tool error instead: exit **2**, `quality_gate_result` `error`. Nor is a scan that
*completed* necessarily a full one: when Chargate could not start a linter it says which,
and Brimyr states that shortfall beside the count rather than passing a smaller scan off
as the whole answer.

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
comment, updated in place on every push rather than stacked, and carrying **both
halves**: the coverage number, the threshold and the changed lines the tests never
executed, then the net-new findings count, its per-level breakdown and what it blocks
on. Set `token_broker_url` as well and it is authored by **Brimyr[bot]** rather than
the shared `github-actions[bot]`. Neither can fail the gate: a comment is a
convenience, and a convenience must never turn a green PR red. See
[PR comment](pr-comment.md).

## Modes

- **PR events** → run tests → patch-coverage gate, plus the quality gate when
  `quality: 'true'` → ship to SonarQube.
- **Push to default branch / scheduled** → run tests → ship to SonarQube as the
  trend baseline → **no** gate. Quality findings are still counted and shown; a
  baseline run has no diff to gate against, and says so.

`mode: auto` (default) picks this from the event; force it with `mode: pr|baseline`.

## Why patch coverage?

Requiring 80% coverage on a whole legacy codebase is a non-starter; ignoring
coverage on new code lets it rot. Patch coverage splits the difference: hold *new
and changed* lines to a bar, leave the back-catalogue alone.

- **Gate** on what *this PR* changed → actionable, no legacy-debt noise.
- **Ship** the full coverage to SonarQube → the long-run trend, and Sonar's own
  quality gate (not to be confused with Brimyr's net-new quality gate above).

## Rollout cost is the point

The comparison that matters is not "which tool computes coverage", it is what it takes
to switch this on across an estate. For eight repos in four languages:

| | SonarQube | Brimyr |
| --- | --- | --- |
| Server to host / licence | a server, or a per-committer plan | none |
| Per repo | a project, a token, a quality gate, workflow YAML | one `uses:` line |
| Per ecosystem | a *different scanner*, `dotnet sonarscanner` for .NET, `mvn sonar:sonar` for Java, the CLI for the rest | detected |
| Producing the coverage report | yours to work out, per language | it runs your tests |
| Per-PR diff coverage | Developer Edition and above | included |

## License

MIT.
