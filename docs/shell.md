# Shell / bats

<!-- sources: src/brimyr/detect.py, src/brimyr/provision.py, src/brimyr/runner.py -->

Brimyr detects a shell repo from a real `.bats` file, runs

```bash
bats --recursive <the directories that hold them>
```

and gates on the exit status. Whether it also gates on *coverage* depends on one thing:
whether `kcov` is installed on the runner. Usually it is not, and that is fine.

## What counts as a shell suite

A `tests/` directory is not evidence of anything. It ships with most repositories in
existence, so the marker only decides whether it is worth looking; the confirming search
is what decides. Brimyr needs an actual `*.bats` file that is not vendored:

- `node_modules`, `.venv`, `vendor` and the other usual directories are skipped, because
  `bats` is itself an npm package and installing it puts its own `.bats` files in the tree.
- `test/bats`, `test/test_helper`, `bats-support`, `bats-assert`, `bats-file`, `bats-mock`
  and `bats-detik` are skipped for the same reason. The conventional bats layout vendors
  the framework as a submodule, and bats-core's own checkout carries a `test/*.bats` suite.
  Without that rule, Brimyr would run the framework's tests instead of yours.

Whatever survives decides the command. The directories holding those files are passed to
`bats --recursive`, a directory already covered by another is dropped, and a `.bats` file
sitting at the repo root is passed as itself rather than as `.` (which would make
`--recursive` walk every vendored checkout in the tree).

```text
tests/scan.bats                  ->  bats --recursive tests
scripts/tests/lib.bats           ->  bats --recursive scripts/tests
tests/a.bats + tests/unit/b.bats ->  bats --recursive tests
smoke.bats                       ->  bats --recursive smoke.bats
```

If your layout defeats that, set `test_command:` and it is used verbatim.

## Running it: `bats` does not need to be installed

If `bats` is not on PATH, Brimyr runs it through `npx --yes bats`, which fetches
bats-core into the runner's npm cache. This is the same route the JavaScript ecosystem
takes with jest, and it exists for the same reason: a shell `127` is a broken run and a
red gate on a repository whose tests are perfectly fine. Set `provision: 'false'` to turn
it off and use whatever the job installed itself.

## Coverage: kcov if you have it, and a green result if you do not

`kcov` is the only realistic coverage tool for bash, and it is installed nowhere by
default. Brimyr therefore:

- **uses it when it is on PATH.** The run is wrapped as
  `kcov --include-path=. --exclude-pattern=... coverage/kcov bats ...`, and the Cobertura
  reports it writes under `coverage/kcov/` are ingested like any others. The `.bats` files
  themselves are excluded, so the denominator is the scripts under test.
- **never installs it.** It is a system package (`apt-get install kcov`) or a source
  build, so provisioning it would mean either `sudo` into the caller's runner image for
  every shell repository on the estate, or minutes of build time per job. Both are the
  wrong trade for a gate that is meant to be free.

So the common case is a bats suite with no coverage at all, and that case is **green**:

```text
⚪ Tests ran, but no coverage was measured, Shell produced no coverage report.
The suite passed; there is simply no number to gate on.
```

`gate_result` is `skipped`, the exit code is `0`, and the summary replaces the coverage
table rather than printing `100% of 0 lines`, which is what a well tested pull request
looks like.

This is not a softening of the broken-run rule. It is a different question: *can* this
ecosystem produce coverage at all? Only `shell` says no. Every other ecosystem
instruments as it runs, so a missing report there still means the run broke and still
turns the build red, including a .NET test project that has no `coverlet.collector`.

To measure your bash, install kcov in the job before the Brimyr step:

```yaml
- run: sudo apt-get update && sudo apt-get install -y kcov
- uses: magmamoose/brimyr@v1
```

## What changes when you pick up this version

Detection is not opt-in, so a repository that already had a bats suite and a Brimyr
workflow starts **running** that suite. If it has never run in CI, expect it to have
opinions. Two ways out, both explicit:

- `ecosystem: 'dotnet'` (or whichever you do want) pins the run to that list, and
  detection is skipped entirely.
- `test_command:` replaces every ecosystem's command with yours.

Nothing else about an existing run changes: a repository with no `.bats` file behaves
exactly as it did.

## Alongside another language

`ecosystem` is comma-separated and detection is not exclusive, so a .NET repository whose
scanners are bash runs both and merges the reports:

```yaml
- uses: magmamoose/brimyr@v1
  with:
    ecosystem: 'dotnet,shell'
    test_command: ''          # leave empty: each ecosystem runs its own command
```

Set `test_command` there and you lose the split, because one command replaces every
ecosystem's. That is the trap this ecosystem exists to remove: forcing
`test_command: 'bats tests'` on a .NET repository traded the .NET coverage for a bats run
that then produced no report and failed the build.

When one half measures and the other does not, the summary says so under the table:

```text
⚠️ Not everything was measured, Shell produced no coverage report, so changed
Shell lines are not in the figures above (they are absent from the denominator,
not counted as uncovered).
```

That warning matters. A file the report never mentions contributes nothing to patch
coverage, so an unmeasured half does not lower the percentage, it narrows what the
percentage is about.

## shellcheck

Not part of this. Static analysis of shell belongs to the [quality
half](quality-findings.md), which runs MegaLinter's linters through Chargate.
`BASH_SHELLCHECK` is a valid MegaLinter key but is not in Chargate's curated `quality`
set, so ask for it explicitly:

```yaml
- uses: magmamoose/brimyr@v1
  with:
    quality: 'true'
    quality_linters: 'BASH_SHELLCHECK,PYTHON_RUFF,JAVASCRIPT_ES,TYPESCRIPT_ES,JAVA_PMD,GO_GOLANGCI_LINT'
```

`quality_linters` **replaces** the curated set rather than adding to it, which is why the
whole list is spelled out above.
