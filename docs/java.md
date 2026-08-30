# Java / JVM

<!-- sources: src/brimyr/detect.py, src/brimyr/coverage/jacoco.py -->

Brimyr detects a Maven repo from `pom.xml`, runs

```bash
mvn -B org.jacoco:jacoco-maven-plugin:prepare-agent test org.jacoco:jacoco-maven-plugin:report
```

and reads every `**/target/site/jacoco/jacoco.xml` it produced.

The plugin goals are invoked **by coordinate**, so this works on a `pom.xml` that has no
JaCoCo plugin configured at all, nothing has to be added to the build to start gating.
If your `pom.xml` already binds `prepare-agent` and `report` to the build lifecycle, a
plain `mvn -B verify` is enough and you can say so:

```yaml
        with:
          test_command: 'mvn -B verify'
```

## JaCoCo XML is not Cobertura

This is the one thing to get right. JaCoCo emits **its own XML format**, and it shares the
`.xml` extension with Cobertura:

```xml
<report name="isam3d-case">
  <package name="nl/example/isam/case">
    <sourcefile name="CaseService.java">
      <line nr="12" mi="0" ci="4"/>   <!-- ci = covered instructions -->
      <line nr="13" mi="3" ci="0"/>   <!-- mi = missed instructions  -->
```

Brimyr tells the two apart by the **root element** (`<report>` vs `<coverage>`), not by the
file name, so `coverage_file: target/site/jacoco/jacoco.xml` just works.

!!! danger "Why the extension is not enough"
    Parsing a JaCoCo file as Cobertura does not fail. It finds no `<class filename=…>`,
    returns an **empty report**, and every changed Java file is then a file the report never
    mentions. Those files leave the denominator, and the gate gladly reports **100% over
    completely untested code**. If a tool ever hands you a coverage number you cannot
    reconcile, this is the first thing to check.

You can always be explicit:

```yaml
        with:
          coverage_file: 'build/reports/jacoco/test/jacocoTestReport.xml:jacoco'
```

### Partially covered lines count as covered

A line with `ci>0` *and* `mi>0` is usually a short-circuited boolean. JaCoCo's own LINE
counter calls it covered and so does diff-cover, so Brimyr does too, otherwise every
`a && b` in a pull request would count against you.

## Multi-module reactors

A multi-module build writes **one report per module**, each under that module's own
`target/`. Brimyr ingests all of them and merges them covered-wins, so a class exercised by
a sibling module's tests comes out covered.

This matters for the same reason it matters on a multi-project .NET solution: a *missing*
report is indistinguishable from *nothing coverable changed*, so dropping one silently
removes that module's changed lines from the denominator and inflates the number. If a
reactor build reports a suspiciously round 100%, count the reports:

```bash
find . -path '*/target/site/jacoco/jacoco.xml' | wc -l   # expect one per module with tests
```

Aggregating into a single report first (the `report-aggregate` goal) also works, point
`coverage_file` at it and Brimyr will not run any tests.

## Source paths

JaCoCo names files as `<package>/<sourcefile>`, `nl/example/isam/case/CaseService.java`, which
is **source-root-relative** and so is missing the `backend/src/main/java/` prefix that
`git diff` reports. Brimyr reconciles that by suffix matching; there is nothing to
configure.

## Gradle

`build.gradle` and `build.gradle.kts` are recognised as Java markers but are **not**
auto-detected, because the built-in command is `mvn` and running it in a Gradle repo would
fail the run and turn the build red. The JaCoCo parser is shared, only the invocation
differs, so name the command:

```yaml
        with:
          ecosystem: 'java'
          test_command: './gradlew test jacocoTestReport'
          coverage_file: 'build/reports/jacoco/test/jacocoTestReport.xml'
```

Gradle's `jacocoTestReport` task writes HTML by default; make sure XML is on
(`reports { xml.required = true }`).

## Excluding generated code

The JVM equivalent of the `.NET` problem: generated sources, MapStruct/Lombok output, JAXB
and OpenAPI stubs. `exclude` drops matching **changed files** from the denominator entirely.
They are not counted as covered, they simply do not count:

```yaml
        with:
          exclude: "**/generated-sources/**,**/target/**,*_MapperImpl.java,**/generated/**"
```

Globs match the repo-relative, forward-slash path and `*` crosses `/`, so a pattern catches
a folder at any depth without you having to know how deep it sits.

JaCoCo's own `<excludes>` configuration works too, and is preferable when you want the
class gone from *all* reporting rather than just from the gate: anything absent from the
report is absent from the denominator already.

## Full example

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
      - uses: actions/setup-java@v4
        with: { distribution: 'temurin', java-version: '21', cache: 'maven' }
      - uses: magmamoose/brimyr@v1
        with:
          checkout: 'false'
          threshold: '80'
          pr_comment: 'true'
          exclude: "**/generated-sources/**,**/generated/**"
```

Start with `mode: baseline`, which computes and reports the number without gating. Once the
numbers look right on real pull requests, switch to the default and pick a threshold from
what you actually saw.

## SonarQube

`sonar.coverage.jacoco.xmlReportPaths` is set from the reports Brimyr found, so the same
run that gates the pull request can also feed the SonarQube trend, non-blocking, as
always.

!!! warning "Java needs `sonar.java.binaries`"
    `sonar-scanner -Dsonar.sources=.` over a Java repo fails outright with *"please
    provide compiled classes with sonar.java.binaries"*. Brimyr cannot infer it, so
    rather than run a scan that cannot succeed it **skips with a warning** until you
    supply it:

    ```yaml
        with:
          sonar_url: https://sonar.example.com
          sonar_args: '-Dsonar.java.binaries=**/target/classes'
    ```

    Sonar also documents that the CLI scanner should not be used for Maven or Gradle
    projects at all, `mvn sonar:sonar` is the supported path and will give a better
    analysis. Brimyr's patch-coverage gate is unaffected either way: it reads the JaCoCo
    reports directly and never talks to SonarQube.
