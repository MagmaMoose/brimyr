# Go

<!-- sources: src/brimyr/detect.py, src/brimyr/coverage/gocover.py, src/brimyr/runner.py -->

Brimyr detects a Go module from `go.mod` plus at least one `_test.go` file that
`go test ./...` would actually run, and runs

```bash
go test -coverprofile=coverage.out ./...
```

Coverage is built into the Go toolchain, so there is nothing to install beyond Go itself:
no plugin, no converter. `go test` fetches the module's dependencies on its own. When the
runner has no `go` on PATH, the action installs one with `actions/setup-go`, at the version
the repo's own `go.mod` asks for. A runner that already has Go keeps its own.

## What counts as a test signal

`go.mod` alone says there is a module, not that anything tests it, so detection also wants
a `_test.go` file belonging to the root module. These do not count:

- anything under `vendor/`, `testdata/`, or a directory whose name starts with `.` or `_`
  (the go tool skips all of them when it expands `./...`);
- a test file inside a **nested module**, a subdirectory with its own `go.mod`. `./...`
  stops at a nested module, so its tests are not the root module's.

A repo whose only module sits in a subdirectory is still found: see
[projects in subdirectories](index.md#projects-in-subdirectories).

## How a coverage profile becomes line coverage

Go does not report lines. It reports **blocks**:

```text
mode: set
github.com/org/repo/internal/names/names.go:85.29,86.13 1 1
github.com/org/repo/internal/names/names.go:86.13,88.3 1 0
```

Each record is a span of source (`startLine.startCol,endLine.endCol`), how many statements
it holds, and how often it ran. Two things have to happen before that can be compared with a
pull request's diff.

**The import path comes off.** The file is named by its module path, which the diff never
carries. Brimyr reads the module path from `go.mod` (every `go.mod` in the repo, longest
first, so a nested module claims its own files), and maps
`github.com/org/repo/internal/names/names.go` back to `internal/names/names.go`.

**The span is trimmed to its statements.** A function's first block starts *at* its opening
brace, on the `func` line, and an `if` body's block ends just past its closing brace. Counting
every line of every span would put the `func` signature, every lone `}`, and every blank or
comment line inside a body into the patch-coverage denominator. Measured on a real operator
codebase, 16% of the lines inside blocks were a lone closing brace. Brimyr reads the source and
trims each block past braces and whitespace at both ends, then drops blank and `//` lines.

What is left is a statement metric again. On this function

```go
func Add(x, y int) int {
    // a comment inside the body

    if x > 100 {
        return 0
    }
    return x + y
}
```

tested with a small `x`, Brimyr counts three lines (the `if`, the early `return`, the final
`return`), two of them covered: 66.7%, the same figure `go test -cover` prints. Counting the
whole spans would have said 71%.

A block with no statements (`func Empty() {}`) is skipped, and a line two blocks share
(`} else {`) is covered if either block ran.

## Untested packages are in the denominator

Since Go 1.22, `go test -coverprofile ./...` includes packages that have no test files,
every block at count 0. A brand-new package nobody tests therefore counts as **uncovered**
rather than vanishing from the denominator, which is the honest answer and not the default
for every ecosystem.

## Supplying a profile yourself

A profile produced elsewhere in your pipeline can be handed over directly. Brimyr recognises
one by its first line (`mode: set`, `count` or `atomic`), whatever the file is called:

```yaml
        with:
          coverage_file: 'coverage.out'
```

`coverage.out:gocover` names the format explicitly. SonarQube receives the same profile
under `sonar.go.coverage.reportPaths`.

## What Brimyr does not do

- **No `-race`.** It needs cgo and a C toolchain the runner may not have, and it measures
  nothing extra. Keep it in your own CI.
- **No `-coverpkg`.** Each package is measured by its own tests, which is what `go test -cover`
  reports and what SonarQube's documentation runs. Coverage one package gets from another
  package's tests is not counted. If you want it, run your own command and pass the profile
  with `coverage_file`.
