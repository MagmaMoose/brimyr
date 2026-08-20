# .NET solutions

<!-- sources: src/brimyr/detect.py, src/brimyr/sonar_dotnet.py, src/brimyr/coverage/cobertura.py -->

Brimyr detects a .NET repo from `*.sln`, `*.csproj`, `*.fsproj` or `*.vbproj`, runs

```bash
dotnet test --collect:"XPlat Code Coverage" --results-directory ./TestResults
```

and reads every `TestResults/**/coverage.cobertura.xml` it produced.

## Multi-project solutions

`dotnet test` on a solution writes **one Cobertura report per test project**, each in its
own `TestResults/<guid>/` directory. Brimyr ingests **all** of them and merges them
covered-wins, so a source file exercised by two different test projects comes out covered
rather than half-covered.

This matters more than it sounds. Patch coverage treats a file the report never mentions
as contributing nothing to the denominator. That is deliberate, and it is what stops an
unrelated language's files from dragging the number around. But it also means that a
*missing* report is indistinguishable from *nothing coverable changed*: every changed file
in the missing project silently leaves the denominator and the gate reports a comfortable
pass. Merging every report is what keeps that from happening.

If you see a suspiciously round 100% on a solution with several test projects, check that
each one actually emitted a report:

```bash
find TestResults -name coverage.cobertura.xml | wc -l   # expect one per test project
```

## Excluding generated code

Scaffolded code is the usual reason a real patch-coverage number is unusable. Nobody
writes tests for an EF Core migration, it can be thousands of lines, and one scaffolded
migration in a pull request can sink an otherwise well-tested change below the threshold.

`exclude` drops matching **changed files** from the denominator entirely. They are not
counted as covered, they simply do not count:

```yaml
      - uses: magmamoose/brimyr@v1
        with:
          exclude: "*Migrations*,*ModelSnapshot*,*.Designer.cs,*AspNetCoreGeneratedDocument*,**/obj/**"
```

Globs are matched against the repo-relative, forward-slash path, and `*` crosses `/`, so
`*Migrations*` catches the folder at any depth without you having to know how deep it sits.

This is the same idea as coverlet's `Exclude` and ReportGenerator's `-classfilters`, and
you can use either instead: anything already absent from the coverage report is absent
from the denominator too. `exclude` is for the case where the file *is* in the report and
you still do not want it gated.

!!! note "Excluding everything is a pass, not a zero"
    A pull request that only touches excluded files has no coverable changed lines, so it
    is a vacuous 100% pass: the same rule that applies to a docs-only change. It is not
    scored 0%.

## Gating, not just reporting

A coverage report published to Pages tells you the number after the fact. It does not stop
a change that lowers it, and on a large codebase the total barely moves anyway, which is
why total-coverage targets get ignored.

Brimyr gates on the **lines the pull request changed**, so the number responds to the work
in front of you, and pre-existing uncovered code never blocks. The two compose: keep
publishing the HTML report if it is useful, and let Brimyr answer "is *this change* tested".

```yaml
name: Coverage
on: [pull_request]

permissions:
  contents: read
  pull-requests: write   # for the PR comment

jobs:
  coverage:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v6
        with: { fetch-depth: 0 }        # patch coverage needs the merge-base
      - uses: actions/setup-dotnet@v4
        with: { dotnet-version: '8.0.x' }
      - run: dotnet build --configuration Release
      - uses: magmamoose/brimyr@v1
        with:
          checkout: 'false'
          threshold: '80'
          pr_comment: 'true'
          exclude: "*Migrations*,*ModelSnapshot*,*.Designer.cs"
```

Start with `mode: baseline`, which computes and reports the number without gating. Once
the numbers look right on real pull requests, switch to the default and pick a threshold
from what you actually saw.

## Mixed .NET and JavaScript

A repo with both `*.sln` and `package.json` is detected as both ecosystems. Brimyr runs
each one's test command, reads Cobertura from one and lcov from the other, and merges them
into a single patch-coverage number, no separate merge step, and no ReportGenerator pass,
required.

If you already produce merged reports another way, feed them in directly and Brimyr will
not run any tests:

```yaml
        with:
          coverage_file: "TestResults/merged.cobertura.xml,web/coverage/lcov.info"
```

## SonarQube

Brimyr runs the **SonarScanner for .NET** automatically when it detects a .NET repo. Set
`sonar_url` and `SONAR_TOKEN`; there is nothing else to configure.

It has to work differently from every other ecosystem, and the difference is not
cosmetic. SonarSource documents that the SonarScanner CLI *"doesn't support C# or VB.NET
analysis"*, C# issues are produced by Roslyn analyzers that `dotnet sonarscanner begin`
injects into the compilation, and `end` *"collects the analysis data generated by the
build"*. No compile between the two steps means no analysis at all. So the scanner
**wraps** the run:

```bash
dotnet sonarscanner begin /k:… /d:sonar.cs.cobertura.reportsPaths=**/TestResults/**/coverage.cobertura.xml
dotnet build --no-incremental --disable-build-servers
dotnet test --collect:"XPlat Code Coverage"      ← Brimyr's normal run
dotnet sonarscanner end
```

Three consequences worth knowing:

* **The coverage path is declared at `begin`**, before any report exists, hence the
  wildcard. `end` accepts only three flags (`sonar.token`,
  `sonar.clientcert.password`, `sonar.scanner.truststorePassword`); everything else must
  be given up front. The wildcard is also what makes a multi-test-project solution work
  without enumerating GUID directories.
* **`--no-incremental` is not optional.** A cached build compiles nothing, so the
  analyzers `begin` injected never run and `end` finds no analysis data, a green run
  that uploaded an empty analysis.
* **`coverage_file` disables it.** If you hand Brimyr a ready-made report there is no
  build, so there is nothing for `end` to collect. Brimyr skips the wrap rather than
  uploading an empty analysis.

!!! note "The property is `reportsPaths`, plural"
    `sonar.cs.cobertura.reportsPaths`, unlike `sonar.python.coverage.reportPaths` and
    `sonar.javascript.lcov.reportPaths`. Sonar is not consistent here, and the singular
    form is silently ignored.

If `end` cannot run or fails, Brimyr removes `.sonarqube/`. Nothing reached the server
either way (upload happens at `end`), but that directory holds the injected MSBuild
hooks and leaving it behind makes the *next* build in the workspace fail for reasons
that look unrelated.
