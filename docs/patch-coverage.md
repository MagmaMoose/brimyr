# Patch coverage

**Patch coverage** is the fraction of *changed executable lines* the test run
covered. A line counts iff it is **changed by the PR** — in an added/modified hunk
on the new side, diffed against `merge-base(base, head)` — **and** the coverage
tool considers it **executable**. The merge-base is robust to base-branch rebases
and force-pushes.

```
            covered changed-executable lines
patch % = ───────────────────────────────────── × 100
              all changed-executable lines
```

## Classification rules

| Case | Behaviour | Configurable |
| --- | --- | --- |
| Brand-new file | every executable line counts | — |
| Modified hunk | only the changed executable lines count | — |
| Pre-existing uncovered line in a changed file | excluded — never penalised | — |
| Blank line / comment / brace | excluded (not in the coverage report) | — |
| Changed file the report never mentions (a doc, an untested new file) | contributes nothing (diff-cover behaviour) | — |
| Renamed / copied file | matched by head path; changed lines line-matched | — |
| Deleted file | dropped | — |
| Nothing coverable changed (docs-only PR) | **vacuous pass** (100%) | — |
| Broken / empty test run | **tool error (exit 2)**, not 0% | — |
| Missing merge-base / shallow clone | **fails loudly** — needs `fetch-depth: 0` | — |

The denominator is deliberately *changed-and-executable*: coverage tools only
report executable lines, so blank lines and comments fall out naturally, and a
genuinely untested new file the suite never imported isn't in the report — it
contributes nothing rather than tanking the score. This matches `diff-cover`.

## Why a broken run is not 0%

If the test command exits non-zero or emits no parseable coverage, reporting that
as "0% patch coverage" would be actively misleading — it conflates *no signal* with
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
blocks. `--no-gate` (or baseline mode) makes the run report-only — coverage is
still computed and shipped to SonarQube, nothing blocks.

!!! tip "Local gate, Sonar trend"
    The gate is computed **locally** from the coverage file — it never depends on
    SonarQube. The `sonar-scanner` run is a separate, non-blocking step that feeds
    Sonar the same coverage for the long-run trend.
