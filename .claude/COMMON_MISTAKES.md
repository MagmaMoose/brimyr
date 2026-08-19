# Common mistakes

- **Broken test run ≠ 0% coverage.** A failed/empty/unparseable run is a tool
  error (exit `2`, build red), never a 0% gate failure — `runner.RunResult.broken`
  + `gate.decide_gate(broken=...)`.
- **The denominator is *changed executable* lines.** Blanks/comments are excluded
  (coverage tools never report them) and files the report doesn't mention
  contribute nothing (diff-cover behaviour). Nothing coverable changed ⇒ vacuous
  100% pass, not a failure.
- **Never import I/O into `coverage/`.** No `subprocess`, `os`, network, or
  Actions code — the purity is the design, not an accident.
- **Never hand-bump the version.** python-semantic-release writes both
  `pyproject.toml` and `src/brimyr/__init__.py`.
- **Shallow clones break merge-base** → `git.ShallowCloneError` → exit 2. CI must
  fetch enough history.
- **Coverage paths rarely equal diff paths.** `patch._match` tries exact, then
  prefix-stripped, then suffix in either direction — fix matching there, never at
  the call sites.
- **.NET has an empty `sonar_property` on purpose** (it needs SonarScanner for
  .NET's begin/end, not a `-D` property). Don't "fix" it.
- **CI runs `ruff format --check` as well as `ruff check`** — format before
  pushing or the build goes red on whitespace.
- **`uv.lock` drifts every release.** semantic-release bumps `pyproject.toml`
  but never re-locks, so `uv.lock`'s `brimyr` version lags. Any `uv run` silently
  fixes it — commit that one-line diff, don't revert it.
