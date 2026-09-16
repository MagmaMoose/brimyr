# 0003 — An ecosystem declares whether it *can* measure; kcov is used, never installed

**Status:** accepted · **Date:** 2026-09-10 · **Context:** the shell ecosystem

## The problem

`runner` treated "the test run produced no coverage file" as a broken run, exit 2, build
red. That is right for pytest, jest, coverlet and JaCoCo, which instrument as they go: no
report means the run broke. It is wrong for `bats`, which emits no coverage at all unless
`kcov` happens to be installed — and kcov is installed nowhere by default.

The consequence was structural, not cosmetic. A repo whose scanners are bash could not be
gated by Brimyr at all: forcing `test_command: 'bats ...'` traded away the coverage of
whatever else the repo was written in, and the bats run then failed the build for
producing no report. `mode: baseline` does not soften it either — baseline suppresses the
*threshold*, not a broken run. That is why
`samenlevingszaken/prlg.sam.certificate.management` hand-wrote a separate `tests.yml`,
and why `Prlg.iSuite.iBeheer` was red (run 210225922).

## The decision

**Split "cannot measure" from "did not measure", and make the ecosystem declare which.**

`Ecosystem.coverage_optional` is a static property of the toolchain, not a per-run
outcome. When it is set, a test run that exits 0 with no report is `RunOutcome.unmeasured`:
green, reported as measuring nothing, and named in the summary. When it is not set,
today's behaviour is untouched — a missing report is still a broken run, so a .NET test
project with no `coverlet.collector` stays red. `shell` is the only row that sets it, and
that scarcity is the whole safety argument: one extra `True` in that table converts a
class of real failures into green gates.

The exit status is not part of the split. A bats suite that fails is broken, and a shell
`127` is still "the toolchain was never installed", never "nothing to measure".

## kcov: used when present, never installed

`provision.py` installs a repo's test dependencies through *the repo's own manager*. kcov
has no such owner: it is `apt-get install kcov` or a source build, so provisioning it
means either `sudo` into the caller's runner image for every shell repo on the estate, or
minutes of build time per job [cost]. Brimyr wraps the run in kcov when kcov is already on
PATH, and otherwise runs bats plain and reports an unmeasured half.

`bats` itself *is* provisioned, through `npx --yes bats`, for exactly the reason every
other provisioning rule exists: shell is auto-detected fleet-wide off a `.bats` file, and
a shell `127` would be a red gate on a repo whose tests are fine.

## The failure mode this creates, and what pays for it

A green run that measured nothing is one step from the vacuous 100% this tool exists to
prevent — and a *half*-measured polyglot run is worse, because the number it prints is
real while covering only part of the diff (a file the report never mentions leaves the
denominator rather than counting as uncovered).

Three renderings pay for it: `no_coverage` replaces the coverage table with its own
wording, distinct from `no_ecosystem`'s; `GateDecision.unmeasured` names the unmeasured
half under the table when another half did measure; and `gate_result` reports `skipped`
rather than `pass`. `unmeasured` is a label and never an input to the verdict.

## Rejected

- **Provisioning kcov.** Cost, and it mutates the caller's runner image.
- **Letting `mode: baseline` cover this.** It suppresses the threshold, not the broken-run
  rule, so it does not solve the problem and it switches off the gate that does work.
- **A per-run "we wrapped with kcov, so a report is now mandatory" flag.** It cannot be
  known when the caller supplies their own `test_command`, so the rule would hold in some
  runs and not others — an exemption nobody can predict is worse than one that always
  applies and is always stated.
