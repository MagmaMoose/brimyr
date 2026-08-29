# Quality findings

<!-- sources: src/brimyr/quality.py, src/brimyr/cli.py, src/brimyr/report.py, action.yml -->

Coverage is half of quality assurance. The other half is **the findings a linter reports
on the lines this PR changed**: and Brimyr gates on those the same way it gates on
coverage: net-new only, pre-existing never blocks.

```yaml
      - uses: magmamoose/brimyr@v1
        with:
          quality: 'true'
```

That runs [Chargate](https://github.com/MagmaMoose/chargate) as a nested step, MegaLinter's quality linters, then net-new classification against the PR diff, and
folds its verdict into the same job summary and the same PR comment as coverage.
**Report-only by default**; see [Start report-only](#start-report-only).

`action.yml` pins `magmamoose/chargate@528a42e` (v2.11.27), which carries the curated
`quality` flavor added in v2.11.26, so `quality: 'true'` is usable.

!!! note "If you pin Brimyr below v1.9.1, leave `quality` off"
    v1.9.0 shipped this feature against `chargate@a852f9c` (v2.11.25), which predates the
    flavor by seventeen seconds of release timing. On that pin the nested step fails, the
    counts file is never written, and Brimyr exits `2` with `quality_gate_result` set to
    `error` : the boundary working as designed, since a gate that cannot evaluate must not
    go green. It is inert rather than dangerous, but it is a red X on every PR.

## The boundary is the subject, not the tool

**Brimyr is quality assurance. Chargate is security assurance.** Brimyr's quality half
calls Chargate's engine, so both tools now emit Chargate-derived findings, but they are
not the same findings and they are not two views of one thing. A *security* finding
belongs to Chargate's gate; a *quality* finding belongs to this one. Read a finding by
its subject, not by which tool surfaced it.

## Brimyr calls Chargate; it does not share a library with it

Chargate already owns a finished net-new engine. `chargate filter-sarif` takes a SARIF
report plus a base and head, and writes two files: the net-new-only SARIF, and a counts
JSON. Brimyr does not import that engine, vendor it, or republish it. It **calls** it,
and gates on what comes back.

Every argument against extracting a shared package, version skew, `uv.lock` drift, a
diamond dependency inside one job, mutating a consumer's interpreter, is an argument
about imports into a shared interpreter. A subprocess in its own environment has none of
those properties, and both tools keep `dependencies = []` literally rather than with an
asterisk.

**Chargate reports; Brimyr gates.** The nested step runs with `fail_on: none`, so it can
never set the job's exit code, `brimyr ci` decides on its own threshold.

## The threshold is a SARIF level, not a severity band

`quality_fail_on` takes one of `none`, `note`, `warning`, `error`, `any`, and blocks on
net-new findings at or above it.

| Value | Blocks on |
| --- | --- |
| `none` | nothing, report-only (the default) |
| `note` | `note`, `warning`, `error` |
| `warning` | `warning`, `error` |
| `error` | `error` only: the equivalent of Chargate's `fail_on: high` |
| `any` | every net-new finding, including ones the linter left unlevelled |

Chargate's own gate speaks severity bands, and copying that vocabulary here would have
been the obvious move. It would also have shipped a threshold that could never fire.

Chargate gates on *per-result* verdicts, where a missing `security-severity` falls back
to the SARIF level (error→high, warning→medium, note→low), so its `fail_on: high` works
perfectly well over a quality scan. Brimyr never sees those verdicts. It sees the counts
document, and there the two breakdowns are **not** interchangeable.

!!! danger "`per_severity_net_new` is empty on a quality scan"
    That map is populated only from a real numeric `security-severity` property, which
    is a security-scanner convention that quality linters essentially never emit.
    `per_level_net_new` covers every result. A band-valued threshold read off this
    document would therefore match nothing, on every PR, forever, while looking exactly
    like a configured gate, so the threshold speaks the vocabulary the document
    actually carries.

## Start report-only

`quality_fail_on` ships as `none` on purpose.

MegaLinter's quality half over a mature repo produces hundreds of net-new findings on
the first pull request, because "changed line touched by a formatter-opinionated linter"
is a far denser event than "changed line with a security finding". The security flavor
is sparse. The quality flavor is not.

The failure mode is **abandonment, not error**: the first real PR goes red, someone sets
the threshold back to `none` to unblock it, and six weeks later the gate is decoration
nobody reads. No test catches that, so the defaults are the mitigation:

1. **Report-only out of the box.** Findings are counted and shown; nothing blocks.
2. **Summary comment only**: no per-finding inline review, and the listing caps at 20
   with the remainder stated. Chargate posts inline comments because security findings
   are rare; these are not.
3. **Five linters, not a flavor's worth**: `GO_GOLANGCI_LINT`, `JAVASCRIPT_ES`,
   `JAVA_PMD`, `PYTHON_RUFF`, `TYPESCRIPT_ES`. Override with `quality_linters`.
4. **`error` before `any`.** Measure a release cycle, then pick a level.

!!! note "There is no .NET linter in the set"
    No C#/VB.NET linter sets `can_output_sarif` at MegaLinter v10.0.0, so none of them
    can reach a SARIF-based gate. A .NET repo turning `quality` on will see zero
    findings, and that is the tooling, not the code. Said here rather than discovered
    six weeks later.

## One run, one comment

Supplying the quality inputs does not add a second gate step or a second PR comment. The
verdict is rendered into the same job summary and the same comment as coverage, a
second heading, `## Brimyr: Net-new findings`, below the coverage block's
`## Brimyr: Quality Assurance`, and the run's exit code is the worse of the two on the
usual scale: `0` pass, `1` a gate blocked, `2` a tool error. What both blocks look like
is on [PR comment](pr-comment.md#one-comment-two-blocks).

A broken test run still reports `2` even when the quality half is clean, and a clean
coverage number does not launder a blocking quality finding.

## It reads counts, not SARIF

Brimyr gates on the counts JSON alone. The net-new SARIF is read only to list `path:line
[rule]` strings in the summary: Brimyr builds no SARIF model and resolves no severity,
because that is Chargate's job and a second implementation here is the coupling the
process boundary exists to avoid.

Three things are therefore hard errors (exit `2`), never a comfortable zero:

* **A `schema_version` Brimyr does not recognise**: including a missing one, which is
  an older Chargate and the same version skew by another route.
* **A counts document that disagrees with itself.** Every net-new result contributes
  exactly one SARIF level, so `per_level_net_new` must sum to `net_new_count`. When it
  does not, one of the two numbers is wrong and nothing on this side can tell which.
* **A net-new SARIF holding a different number of results than the counts claim.**
  Chargate writes both files from one in-memory result, so a mismatch means one is stale
  or truncated.

Each of these would otherwise present as "0 net-new findings", which is
indistinguishable from a clean PR. **Check the denominator before believing a good
result.**

## A scan that completed is not necessarily a full one

An exit-`0` Chargate run is not proof the scan was **complete**. Chargate can decline to
start a linter, no image for the runner's architecture, no SARIF output, the linter
disabled, and still exit `0`. Whatever that linter would have reported is then simply
absent from the count, and absent findings are exactly what a clean repository looks
like.

That shortfall is neither a failure nor a finding, so it is neither gated on nor
swallowed: it is **said out loud**. Chargate names the linters it could not run, the
action forwards that list as `--quality-scan-note` (`--scan-note` on `brimyr lint`), and
the summary states it beside the number it qualifies:

> ⚠️ **The scan was not complete**: these linters did not run: … Anything they would
> have reported is missing from the count above.

It never blocks. A missing linter image is not the pull request's fault, and failing on
one would be its own kind of noise, but a count produced by half a scan must not be
presented as though it were the whole answer.

## Running it without the action

`brimyr lint` is the same gate over files you already have, Chargate's output from a
previous step, or a run of `chargate filter-sarif` by hand:

```sh
chargate filter-sarif --sarif full.sarif --base "$BASE" --head HEAD \
    --out net-new.sarif --counts-json counts.json --no-gate

brimyr lint --counts counts.json --findings net-new.sarif --fail-on error
```

Run on its own it posts under its **own** PR comment marker, so it cannot overwrite the
coverage comment. The consolidated single comment comes from passing the same two files
to `brimyr ci` instead, which is what the action does. See [CLI reference](cli.md).

## Failure isolation, and its limit

The nested Chargate step is `continue-on-error`: a MegaLinter or Docker failure must not
take the coverage gate down with it. That is the same contract as the
[SonarQube](sonarqube.md) and [HTML report](html-report.md) legs.

It stops there, and deliberately. `action.yml` branches on the nested step's own
`outcome`, and on anything other than `success` it hands Brimyr `--quality-scan-broken`
instead of a counts path. That flag reads nothing at all: it reports a scan that did not
complete, exit `2`, `quality_gate_result` `error`. **A quality scan that never ran must
not read as a clean quality half**: it just should not also erase the half that did
work.

The outcome is what it trusts because the file proves nothing. `chargate ci` writes its
counts JSON *before* it decides whether the scan produced any runs, so a scan that ran
nothing leaves a well-formed **row of zeros** on disk and only then exits `2`. Read on
its own, that document is indistinguishable from a clean pull request; the step's
outcome is the only signal that tells the two apart.

The other path is real, just not the one the action takes: a counts file passed
explicitly to `brimyr lint --counts` that is missing or unreadable is still exit `2`,
with a message saying it could not be read.
