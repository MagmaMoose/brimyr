# Common mistakes

- **Broken test run ≠ 0% coverage.** A failed/empty/unparseable run is a tool
  error (exit `2`, build red), never a 0% gate failure — `runner.RunResult.broken`
  + `gate.decide_gate(broken=...)`.
- **The denominator is *changed executable* lines.** Blanks/comments are excluded
  and files the report doesn't mention contribute nothing (diff-cover behaviour).
  Nothing coverable changed ⇒ vacuous 100% pass, not a failure.
- **Never import I/O into `coverage/`.** No `subprocess`, `os`, network, or
  Actions code — the purity is the design, not an accident.
- **Never hand-bump the version.** python-semantic-release writes both
  `pyproject.toml` and `src/brimyr/__init__.py`. It does not re-lock, so
  `uv.lock` lags a release — any `uv run` fixes it; commit that diff.
- **`.xml` is not a format.** Cobertura and JaCoCo share it; parsing JaCoCo as
  Cobertura yields an *empty* report → vacuous 100% over untested code.
  `cli._sniff_xml_format` picks by root element; never re-add `.xml` to `_EXT_FORMAT`.
- **Total coverage is not a `sum(report.files)`.** `merge_reports` keys by exact path
  string, so one file rooted two ways (multi-project .NET) counts twice — measured, 50%
  where the truth is 100%. Use `compute_total_coverage`, which folds by path-suffix and
  applies `exclude_globs`. Empty denominator ⇒ `None`, never the gate's vacuous 100%.
- **.NET Sonar must WRAP build+test** (`sonar_dotnet.session`) — the CLI scanner cannot
  analyze C# at all, and `--no-incremental` is load-bearing (a cached build compiles
  nothing → empty analysis). Sonar needs an installed scanner *and* a project key; both
  were missing, so it silently "skipped" while builds stayed green. Warn via
  `::warning::`, never bare stderr.
- **Sample-size floor**: `min_lines` (default 20, matching SonarQube) skips the
  threshold under 20 changed executable lines. Tests asserting threshold behaviour on
  small fixtures need `min_lines=0` or they pass for the wrong reason. Never silent.
- **Coverage paths rarely equal diff paths.** `patch._match` tries exact, then
  prefix-stripped, then suffix either way — fix matching there, not the callers.
- **Shallow clones break merge-base** → `ShallowCloneError` → exit 2.
- **`broker/` is a separate project** with its own deps, ruff config and CI job.
  Root ruff excludes it and root pytest ignores it; run `make -C broker test`.
- **Cost is a correctness property.** `memory_size` >512 MB pushes Lambda compute
  out of the always-free 400k GB-s under load, and `throttle_rate_limit` raises
  the ceiling linearly — sustained abuse is ~$2.91/mo at 1 rps/512 MB vs ~$16.81
  at the module defaults. The broker account has **no 12-month free tier**: API
  Gateway and S3 bill from unit one, while Lambda, Logs, SSM, ACM and SNS are
  always free. A two-label hostname costs ~$10/mo (Cloudflare Universal SSL
  covers the apex and one label only).
