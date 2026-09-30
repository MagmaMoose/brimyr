# 0004: Projects below the root are a fallback, and only for suites that prove themselves

**Status:** accepted · **Date:** 2026-09-30 · **Context:** ecosystem detection

## The problem

`_has_marker` globs the repo root only. MagmaMoose/dunmir keeps everything in `agent/`,
`backend/` and `frontend/`, each with its own manifest and suite, and nothing at the root,
so Brimyr reported "no test suite detected" on MagmaMoose/dunmir#230 over roughly 2,500
tests. A green `skipped` that gates nothing, on a repo that plainly has tests.

## The decision

**Search below the root only when the root detects nothing.** `_detect_nested` walks up to
three directories down, and every project it finds runs from its own directory with its
own environment; `Ecosystem.project_dir` carries where, and the runner maps the report's
project-relative paths back onto the repo.

A fallback and not a second pass, because a second pass changes verdicts that exist
today. caldrith is a root python project with `console/backend` (whose test deps live in
an extra `uv run` does not sync) and `console/frontend` (no test files yet): searching
under a detected root would turn both red the day the release landed, with no change on
their side. As a fallback, the only runs that change are ones that currently report "no
test suite detected" and gate nothing.

**Only `python`, `javascript` and `go` are searched.** Their `confirm` predicates prove a
SUITE exists (a test file, a declared test script). Java's proves only a `pom.xml` and
.NET has none, so below the root they would run `mvn test` / `dotnet test` in every
library module and go red where nothing is tested; .NET is also wrapped by the Sonar
scanner from the root and has to agree with `action.yml`'s scanner install. Shell's search
is already repo-wide.

## Consequences

- A found directory is not descended into, so a JS workspace whose root script runs every
  package does not run each package twice.
- Fixtures, build output, vendored and hidden trees are never entered: a fixture repo is
  shaped like a project on purpose.
- Paths are rebased only where the file exists under the project. An invented path would
  match nothing and silently leave the denominator; a path left alone keeps its suffix
  match.
- A forced `--ecosystem` still means the root. Nested sub-projects of a repo whose root IS
  detected stay unrun, which is the status quo, not a regression; widening that is a
  separate decision that has to price the verdicts it would change.
