# Patch coverage

<!-- sources: src/brimyr/coverage/patch.py, src/brimyr/coverage/diff.py, src/brimyr/gate.py -->

**Patch coverage** is the fraction of *changed executable lines* the test run
covered. A line counts iff it is **changed by the PR**: in an added/modified hunk
on the new side, diffed against `merge-base(base, head)`, **and** the coverage
tool considers it **executable**. The merge-base survives base-branch rebases
and force-pushes.

```text
            covered changed-executable lines
patch % = ───────────────────────────────────── × 100
              all changed-executable lines
```

## Classification rules

| Case | Behaviour | Configurable |
| --- | --- | --- |
| Brand-new file | every executable line counts | none |
| Modified hunk | only the changed executable lines count | none |
| Pre-existing uncovered line in a changed file | excluded: never penalised | none |
| Blank line / comment / brace | excluded (not in the coverage report) | none |
| Changed file the report never mentions (a doc, an untested new file) | contributes nothing (diff-cover behaviour) | none |
| Renamed / copied file | matched by head path; changed lines line-matched | none |
| Deleted file | dropped | none |
| Nothing coverable changed (docs-only PR) | **vacuous pass** (100%) | none |
| Broken / empty test run | **tool error (exit 2)**, not 0% | none |
| Missing merge-base / shallow clone | **fails loudly**: needs `fetch-depth: 0` | none |

The denominator is deliberately *changed-and-executable*: coverage tools only
report executable lines, so blank lines and comments fall out naturally, and a
genuinely untested new file the suite never imported isn't in the report, it
contributes nothing rather than tanking the score. This matches `diff-cover`.

## Why a broken run is not 0%

If the test command exits non-zero or emits no parseable coverage, reporting that
as "0% patch coverage" would be actively misleading: it conflates *no signal* with
*bad signal*. Brimyr treats it as a **tool error (build red, exit 2)** instead, the
same philosophy as a broken security scanner being a tool error, not a finding.

## Path matching

Coverage-report paths and `git diff` paths rarely match byte-for-byte: coverage
tools emit absolute paths, `<source>`-rooted paths, or monorepo-prefixed paths,
while the diff is repo-relative. Matching therefore falls back:

1. **exact** normalized-path match;
2. a coverage path that **ends with** the diff path (absolute coverage path);
3. a diff path that ends with a (shorter) coverage path.

Pass `strip_prefix` (action) / `--strip-prefix` (CLI) to peel a known root (a
monorepo subdir, a Cobertura `<source>`) before matching.

## The threshold

`threshold` (default **80**) is the patch-coverage percentage below which the gate
blocks. `--no-gate` (or baseline mode) makes the run report-only: coverage is
still computed and shipped to SonarQube, nothing blocks.

### The sample-size floor

`min_lines` (default **20**) is the number of changed executable lines below which the
threshold is **not applied**. Below that the percentage is too coarse to act on: one
uncovered line out of three is 67%, which fails an 80% gate while telling you nothing
useful about the change.

SonarQube applies exactly this rule at exactly this number: *"the conditions on coverage
are ignored until the number of new lines to cover is at least 20"*, so matching it stops
a Brimyr verdict and a SonarQube verdict from disagreeing on small pull requests.

!!! warning "It is a hole, deliberately"
    A 19-line change with no tests at all passes. That is the same *shape* of hole as the
    one that makes a project-level coverage gate unusable, just much smaller. The
    difference is that Brimyr **says so**:

    ```text
    ⚪ Only 3 changed executable line(s), below the 20-line minimum, so the 80.0%
    threshold was not applied (patch coverage was 33.3%).
    ```

    SonarQube applies the same exemption silently, which is how a team ends up believing
    small PRs are gated when they are not. Set `min_lines: '0'` to gate every diff.

A diff with **no** coverable lines at all is still a separate case, a vacuous pass, the
same as a docs-only change, and is reported as such rather than as "too small".

!!! tip "Local gate, Sonar trend"
    The gate is computed **locally** from the coverage file, it never depends on
    SonarQube. The `sonar-scanner` run is a separate, non-blocking step that feeds
    Sonar the same coverage for the long-run trend.

## Total coverage, reported next to it

Brimyr also reports an **overall** coverage figure alongside the patch number. It is
reported and never gated on: the same split Chargate uses, where the build blocks on
net-new findings but the full SARIF still ships.

```text
| Patch coverage                             | **100.0%** |
| Covered / changed executable lines         | 2 / 2      |
| Total coverage (measured files)            | 7.3%       |
| Covered / executable lines across 2 file(s)| 3 / 41     |
```

The gate is unchanged: `gate_result`, the exit code and `failed` all still derive from
patch coverage alone. A codebase at 7% total does not fail a well-tested PR.

### What the number actually means

**"Total coverage (measured files)", not "project coverage".** A coverage report only
mentions files the test run loaded, so a module no test imports is absent from the
report and therefore absent from this denominator, which means **adding a brand-new
untested file can raise it**. Say "coverage of what we measured".

It also will not match SonarQube's figure, which applies its own
`sonar.coverage.exclusions` and source scoping. That is fine and expected: this number
is the at-a-glance trend inside a PR comment, and **SonarQube owns the authoritative
long-run total**. If you only want one number, use Sonar's.

!!! note "Nothing measured is not 100%"
    When a run measures no executable lines, total coverage is reported as *absent*, not
    as 100%. Patch coverage's vacuous 100% exists because a docs-only PR genuinely has
    nothing to cover; a total of 100% over zero lines would just be a lie that looks
    like good news. The `total_coverage` action output is an **empty string** in that
    case, never `0.00`.

### It obeys `exclude`

The same globs that drop generated code from the patch denominator drop it here. They
have to: a comment showing a patch figure that ignored the EF migrations next to a total
that didn't would disagree by twenty points and read as a bug in the tool.

### Baseline mode

In baseline (report-only) mode the patch rows are **omitted** entirely rather than
printed as a vacuous `100.0% (0 / 0)`, because baseline computes patch coverage against
an empty diff. Baseline plus total coverage is the useful combination for a repo with no
PR CI to attach a gate to: run it on pushes to the default branch, block nothing, and
start producing a trend.
