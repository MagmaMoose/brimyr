# 0002 — The quality gate calls Chargate; it does not share a library with it

**Status:** accepted · **Date:** 2026-08-21 · **Context:** MagmaMoose/brimyr#33

## The decision that was asked for

Brimyr is quality assurance and Chargate is security assurance, but Brimyr only ever covered
half of its own remit: test coverage. The other half — gating on net-new *quality* findings —
needs a net-new engine, and Chargate already has one. brimyr#33 asks which way that goes.

**Decision: a process boundary.** Brimyr shells out to `chargate filter-sarif` (in CI, a nested
`uses: magmamoose/chargate@<sha>` step) and gates on the two files it leaves behind. It does
not import Chargate, vendor it, or republish it.

## Why not a shared package

Chargate's `filter-sarif` is *already* a finished, machine-readable net-new service. It takes a
SARIF path plus base/head, never touches MegaLinter, and writes a filtered SARIF and a counts
JSON. Brimyr does not need the code; it needs the answer.

Every argument against extracting a shared package — version skew, `uv.lock` drift, a diamond
dependency inside one job, mutating a consumer's interpreter — is an argument about **imports
into a shared interpreter**. A subprocess in its own environment has none of those properties.
Both packages keep `dependencies = []` literally rather than with an asterisk, which is a
stated design property in four files on each side.

The release-train objection dissolves the same way: a SHA-pinned nested action is a Dependabot
bump, arriving in the same PR shape as every other pinned action in the repo.

## What stays deliberately duplicated

Chargate's `sarif/diff.py` and `git.py` are near-identical to Brimyr's `coverage/diff.py` and
`git.py` — roughly 330 lines. They stay duplicated, because **under this design the two copies
never answer the same question.** Brimyr's parser only ever feeds coverage; Chargate's only
ever feeds findings. A divergence is a cosmetic inconsistency between two independent gates,
not a wrong verdict inside one.

The insurance is `tests/fixtures/diff_corpus/` — real `git diff` output plus hand-verified
expected ranges, vendored into both repos with a checksum test that fails if the corpora drift
apart (#34). That catches the divergence actually worth fearing, without a package boundary.

**Tripwire for revisiting:** a *third* Python consumer of the diff engine **and** one observed
bug that had to be fixed in both repos. Diatreme going Python does not trigger it — that work
is the token broker, and Diatreme has no diff or merge-base code because it does not gate on
changed lines at all.

## The boundary

| | Owns |
| --- | --- |
| **Chargate** | MegaLinter invocation, SARIF parsing, net-new classification, suppressions. A `quality` entry in `FLAVOR_STANDALONE_SETS`, and a documented-stable `filter-sarif` contract. |
| **Brimyr** | The quality *product* — thresholds, verdict wording, job summary, PR comment, one consolidated view next to coverage. |

**Chargate reports; Brimyr gates.** The nested step runs with `fail_on: none` so it can never
set the job's exit code, and `brimyr ci` decides on its own threshold.

## Two consequences worth writing down

### The gate reads counts, not SARIF

`brimyr lint` gates on the counts JSON alone. The filtered SARIF is read only to list
`path:line [rule]` strings in the summary — it builds no SARIF model and resolves no severity,
because that is Chargate's job and a second implementation of it here is exactly the coupling
the boundary exists to avoid. brimyr#33 says it outright: *touches no diff parsing, no git, no
SARIF model.*

That is also why a filtered SARIF whose result count contradicts `net_new_count` is exit 2
rather than a number brimyr picks a winner between. Chargate writes both files from one
in-memory result, so a mismatch means one of them is stale — and in this codebase the stale one
is always the one that would have made the PR pass.

### `fail_on` speaks SARIF levels, not severity bands

Copying Chargate's vocabulary was the obvious move, and it would have shipped a threshold that
could never fire.

Chargate gates on **per-result verdicts**, where `effective_band` uses a numeric
`security-severity` when the tool emits one and otherwise derives a band from the SARIF level
(error→high, warning→medium, note→low). Its `fail_on: high` therefore works perfectly well over
a quality scan. Brimyr never sees those verdicts — it sees the counts document, and there the two
breakdowns are **not** interchangeable: `per_severity_*` is populated *only* from a real
`security-severity` property, which quality linters essentially never emit, while `per_level_*`
covers every result.

So Brimyr's `fail_on` is `none | note | warning | error | any` — the vocabulary the document it
reads actually carries, every value of which is reachable. Chargate's `fail_on: high` is Brimyr's
`fail_on: error`. The divergence is deliberate: a band threshold read off the counts JSON would
match nothing, on every PR, forever, while looking exactly like a configured gate.

## The risk that actually matters

Not the plumbing — **the noise.** MegaLinter's quality half over a mature repo produces
hundreds of net-new findings on the first pull request, because "changed line touched by a
formatter-opinionated linter" is a far denser event than "changed line with a security
finding".

It fails by **abandonment, not by error**: the first real PR goes red, `fail-on: none` gets set
to unblock, and six weeks later the gate is decoration nobody reads. No test catches that, so
the mitigations are structural:

1. **Report-only by default.** `quality_fail_on` ships as `none`.
2. **Summary comment only.** No per-finding inline review, and the listing is capped at 20 with
   the remainder stated. Chargate's inline comments are viable because security findings are
   rare.
3. **Five linters, not a flavor's worth.** Chargate's `quality` set is GO_GOLANGCI_LINT,
   JAVASCRIPT_ES, JAVA_PMD, PYTHON_RUFF, TYPESCRIPT_ES — chosen for signal over style, and
   grown from evidence rather than up front.
4. **`fail_on: error` before `fail_on: any`.**

## What must NOT happen

- **Brimyr must never import `chargate`.** The moment it does, every objection above becomes
  true again and `dependencies = []` becomes a footnote.
- **Chargate must never gate for Brimyr.** The nested step is `fail_on: none`; a verdict
  arriving as someone else's exit code is a boundary that has stopped meaning anything.
- **A version brimyr does not recognise must not pass.** The counts JSON carries
  `schema_version` and Brimyr hard-fails (exit 2) on a value outside its supported set —
  including a missing one, which is an old Chargate and the same skew by another route. This is
  the rule Chargate already enforces for an empty SARIF: a gate that cannot evaluate must not
  report success.
- **A counts file must never be taken as proof the scan ran.** `chargate ci` writes it before it
  checks whether the scan produced any runs, so a scan that found nothing leaves a well-formed
  row of zeros behind and only then exits 2. The action trusts the nested step's `outcome` and
  passes `--quality-scan-broken`, which skips every read. Zero findings and no scan look
  identical on disk, and only one of them is a passing PR.
