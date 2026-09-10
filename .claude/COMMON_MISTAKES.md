# Common mistakes

Always-applicable. Area-scoped traps — ecosystem detection, coverage formats and report
merging, dependency provisioning, Sonar, `broker/`, cost, CI gates, the quality half, the
shared diff corpus — live in `SUBSYSTEMS.md`. Read that section when you touch one.

- **Broken test run ≠ 0% coverage.** A failed/empty/unparseable run is a tool error
  (exit `2`, build red), never a 0% gate failure — `runner.RunResult.broken` +
  `gate.decide_gate(broken=...)`.
- **The denominator is *changed executable* lines.** Blanks/comments are excluded, and
  files the report never mentions contribute nothing (diff-cover behaviour). Nothing
  coverable changed ⇒ vacuous 100% pass, not a failure.
- **Bugs here are silent passes, not crashes.** An unmatched path, a report parsed as
  the wrong format, a dropped report — each removes files from the denominator and
  returns a comfortable number. Check the denominator before believing a good result.
- **An empty report has THREE causes and only one is red.** A failed run (`broken`) is a
  tool error; a real 0% is a measurement; a repo with no suite is `no_ecosystem` — green,
  and its summary REPLACES the coverage table so it can never read as a vacuous
  `100% · 0/0`. Never let those two renderings converge. Detail: `SUBSYSTEMS.md`.
- **Never import I/O into `coverage/`.** No `subprocess`, `os`, network or Actions code —
  the purity is the design, not an accident.
- **Never hand-bump the version.** python-semantic-release writes both `pyproject.toml`
  and `src/brimyr/__init__.py`. It does not re-lock, so `uv.lock` lags a release — any
  `uv run` fixes it; commit that diff.
- **Coverage paths rarely equal diff paths.** `patch._match` tries exact, then
  prefix-stripped, then suffix either way — fix matching there, not in the callers.
- **Sample-size floor.** `min_lines` (default 20, matching SonarQube) skips the threshold
  under 20 changed executable lines. Tests asserting threshold behaviour on small
  fixtures need `min_lines=0` or they pass for the wrong reason. Never silent.
- **Detecting a suite ≠ being able to run it.** `provision.py` installs the repo's deps
  through its own manager before the tests; a shell `127` is reported as *nothing ran*,
  never as "did the run emit coverage?", and provisioning that declines must say why.
  Detail: `SUBSYSTEMS.md`.
- **Shallow clones break merge-base** → `ShallowCloneError` → exit 2.
- **`1` is a verdict on the PR; every other failure is `2`.** A setup error arriving as
  `1` says "your coverage is too low" about a missing binary — raw `OSError` from a
  subprocess is the usual leak, and a bad `--repo` used to pass GREEN. Mirror image: an
  output sink (`--json-out`, HTML, comment, Sonar) never fails the gate. `SUBSYSTEMS.md`.
