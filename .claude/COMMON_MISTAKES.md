# Common mistakes

Always-applicable. Subsystem-scoped traps (Sonar, `broker/`, cost, CI gates, the shared
diff corpus) are in `SUBSYSTEMS.md` — read that when you touch one of those.

- **Broken test run ≠ 0% coverage.** A failed/empty/unparseable run is a tool error
  (exit `2`, build red), never a 0% gate failure — `runner.RunResult.broken` +
  `gate.decide_gate(broken=...)`.
- **The denominator is *changed executable* lines.** Blanks/comments are excluded, and
  files the report never mentions contribute nothing (diff-cover behaviour). Nothing
  coverable changed ⇒ vacuous 100% pass, not a failure.
- **Bugs here are silent passes, not crashes.** An unmatched path, a report parsed as
  the wrong format, a dropped report — each removes files from the denominator and
  returns a comfortable number. Check the denominator before believing a good result.
- **Never import I/O into `coverage/`.** No `subprocess`, `os`, network or Actions code —
  the purity is the design, not an accident.
- **Never hand-bump the version.** python-semantic-release writes both `pyproject.toml`
  and `src/brimyr/__init__.py`. It does not re-lock, so `uv.lock` lags a release — any
  `uv run` fixes it; commit that diff.
- **`.xml` is not a format.** Cobertura and JaCoCo share it; parsing JaCoCo as Cobertura
  yields an *empty* report → vacuous 100%. `cli._sniff_xml_format` picks by root element;
  never re-add `.xml` to `_EXT_FORMAT`.
- **Total coverage is not `sum(report.files)`.** `merge_reports` keys by exact path
  string, so one file rooted two ways (multi-project .NET) counts twice — measured 50%
  where the truth is 100%. Use `compute_total_coverage`: it folds by path-suffix and
  applies `exclude_globs`. Empty denominator ⇒ `None`, never the gate's vacuous 100%.
- **JaCoCo paths carry no module prefix.** Two modules' `nl/x/Service.java` are the
  same string, so `merge_reports` folds them covered-wins and the covered module
  answers for the uncovered one: measured 100% where the truth was 0%.
  `runner._jacoco_path_resolver` reconstructs `<module>/<src-root>/...` from disk.
- **Coverage paths rarely equal diff paths.** `patch._match` tries exact, then
  prefix-stripped, then suffix either way — fix matching there, not in the callers.
- **Sample-size floor.** `min_lines` (default 20, matching SonarQube) skips the threshold
  under 20 changed executable lines. Tests asserting threshold behaviour on small
  fixtures need `min_lines=0` or they pass for the wrong reason. Never silent.
- **Shallow clones break merge-base** → `ShallowCloneError` → exit 2.
