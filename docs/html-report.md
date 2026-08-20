# HTML coverage report

<!-- sources: src/brimyr/html_report.py, action.yml -->

Brimyr can render the coverage it produced as a **browsable HTML report** and upload it
as a build artifact, so a repo with no SonarQube still has something to look at.

```yaml
      - uses: magmamoose/brimyr@v1
        with:
          html_report: 'true'
```

Download `brimyr-coverage-html` from the run's artifacts and open `index.html`.

## It wraps ReportGenerator

[ReportGenerator](https://github.com/danielpalme/ReportGenerator) (Apache-2.0) does the
rendering. That is not a shortcut. It is the only sane choice: it **sniffs each input
file independently**: by XML root element, or a `TN:`/`SF:` prefix for lcov, so the
whole list Brimyr produced goes over in one `-reports:` argument with no branching on
format. A polyglot repo gets **one** report covering its Cobertura and its lcov together.

Every alternative is single-format. `genhtml` is lcov-only, `coverage html` needs the
binary `.coverage` file, and JaCoCo's own CLI wants `.exec` plus compiled classes rather
than the XML. Any of them would mean a converter per ecosystem and three moving parts
instead of one.

## Why an artifact and not a URL

A short-lived hosted page sounds better and is worse. It means storage, auth, expiry and
a leak surface, coverage percentages, repo names and the directory tree of a private
repo, in exchange for saving a download. The artifact gives you almost all of the value
and none of that.

## Cost

ReportGenerator is framework-dependent .NET; there is no self-contained build.

* **GitHub-hosted runners**: free. The .NET SDK is preinstalled on all three images, so
  `dotnet tool install --global` needs no project and no setup step.
* **Container or self-hosted runners**: add `actions/setup-dotnet` before Brimyr.
  Without it Brimyr emits a warning and skips the report; the gate is unaffected.

This is why `html_report` is **off by default**. It is not a price to charge a Python
repo that never asked for it.

## Failure isolation

Same contract as the SonarQube leg: a missing renderer, a missing runtime or a malformed
report is a `::warning::` and the run continues. **The gate never depends on it.**

!!! note "Brimyr renders its own PR comment"
    ReportGenerator has a `MarkdownSummaryGithub` report type, and Brimyr deliberately
    does not use it: it is whole-project: the opposite of what this tool gates on, and
    the free build writes a `reportgenerator.io/pro` upsell row into the table. The PR
    comment stays Brimyr's own.
