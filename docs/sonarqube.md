# SonarQube

<!-- sources: src/brimyr/sonar.py, src/brimyr/sonar_dotnet.py, src/brimyr/detect.py, action.yml -->

Brimyr can ship coverage to SonarQube in the same run that gates the pull request. Set two
inputs and the action installs a scanner for you:

```yaml
      - uses: magmamoose/brimyr@v1
        with:
          sonar_url: ${{ vars.MM_SONAR_URL }}
          sonar_token: ${{ secrets.MM_SONAR_TOKEN }}
```

An empty `sonar_url` disables the whole leg, so passing an unset variable simply turns it
off. The project key defaults to the repo slug with `/` replaced by `_`, because Sonar
keys can't contain a slash.

!!! note "This is a full analysis, not a coverage upload"
    Running the scanner performs SonarQube's own analysis: bugs, code smells, duplication
    and security hotspots arrive with the coverage whether or not you look at them. There
    is no coverage-only mode.

## The gate never depends on it

Every failure here is a `::warning::` annotation and the run continues. A missing scanner,
a bad URL, an outage, an absent token: none of them change the exit code. The patch-coverage
gate is computed locally from the coverage file and never talks to SonarQube.

## One scanner per ecosystem

| Ecosystem | Scanner | Coverage property |
| --- | --- | --- |
| Python | `sonar-scanner` (after the tests) | `sonar.python.coverage.reportPaths` |
| JavaScript / TypeScript | `sonar-scanner` (after the tests) | `sonar.javascript.lcov.reportPaths` |
| Java | `sonar-scanner` (after the tests) | `sonar.coverage.jacoco.xmlReportPaths` |
| .NET | `dotnet sonarscanner`, **wrapping the build** | `sonar.cs.cobertura.reportsPaths` |

.NET is the one that can't follow the same shape. SonarSource documents that the
SonarScanner CLI doesn't support C# or VB.NET at all, because those issues come from Roslyn
analyzers that `dotnet sonarscanner begin` injects into the compilation, and `end` collects
what the build produced. So for .NET the scanner wraps the run:

```text
dotnet sonarscanner begin /k:... /d:sonar.cs.cobertura.reportsPaths=**/TestResults/**/coverage.cobertura.xml
dotnet build --no-incremental --disable-build-servers
dotnet test --collect:"XPlat Code Coverage"
dotnet sonarscanner end
```

Brimyr does this automatically when it detects a .NET repo. Three consequences:

- The coverage path is declared at `begin`, before any report exists, so it's a wildcard.
  `end` accepts only three flags, and the wildcard is also what makes a multi-test-project
  solution work without naming GUID directories.
- `--no-incremental` isn't optional. A cached build compiles nothing, the injected analyzers
  never run, and `end` uploads an empty analysis from a green job.
- Setting `coverage_file` disables the wrap. With no build there's nothing for `end` to
  collect, so Brimyr skips rather than uploading an empty analysis.

When the .NET scanner runs, the plain `sonar-scanner` pass doesn't also run. The .NET scanner
analyses the whole tree, and a second pass on the same project key would overwrite it.

!!! note "The .NET property is `reportsPaths`, plural"
    Unlike `sonar.python.coverage.reportPaths` and `sonar.javascript.lcov.reportPaths`.
    Sonar isn't consistent here, and the singular form is silently ignored.

## Java needs `sonar.java.binaries`

`sonar-scanner` over a Java repo fails with `please provide compiled classes with
sonar.java.binaries`. Brimyr can't infer the path, so rather than run a scan that can't
succeed it skips with a warning until you supply it:

```yaml
        with:
          sonar_url: https://sonar.example.com
          sonar_args: '-Dsonar.java.binaries=**/target/classes'
```

Sonar also documents that the CLI scanner shouldn't be used for Maven or Gradle projects at
all. `mvn sonar:sonar` gives a better analysis. The patch-coverage gate is unaffected either
way: it reads the JaCoCo reports directly.

## What the scanner install does

The install step only runs when `sonar_url` is set, and it carries `continue-on-error`, so a
failure leaves no scanner on `PATH` and Brimyr reports `skipped` instead of failing your job.

- **.NET repos** get `dotnet tool install --global dotnet-sonarscanner`. Already-installed
  isn't an error: it falls through to `update`, then to using what's there.
- **Everything else** downloads the SonarScanner CLI and verifies it against SonarSource's
  published `.sha256` rather than a hash pinned in this repo, which would go stale the
  moment `sonar_scanner_version` is bumped.

## Community Build can't gate a pull request

Worth knowing before you plan around it. SonarQube Community Build analyses **only the main
branch**. Pull request analysis and branch analysis start at Developer Edition.

You can still run a scan in a `pull_request` job, and `sonar.qualitygate.wait=true` will fail
that job on a red gate. But it isn't a pull request analysis: with no `sonar.pullrequest.*`
support the results are filed against the project's single main branch, so the gate is
evaluated against main's new code and the run overwrites main's stored state with PR code.
Using Sonar as the PR gate destroys the trend you're keeping Sonar for.

The Sonar way quality gate does include `New code test coverage is greater than or equal to
80.0%`, but "New Code" there means everything since the project's baseline, not the lines
this pull request changed. That gate also ignores coverage entirely until there are at least
20 new lines to cover.

!!! warning "Never pass branch parameters to Community Build"
    `sonar.branch.name` and `sonar.pullrequest.*` are a hard scanner error against Community
    Build, not a silent no-op. Brimyr doesn't pass either, but `sonar_args` would let you.
