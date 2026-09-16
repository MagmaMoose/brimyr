"""Unit tests for ecosystem detection (brimyr.detect)."""

from __future__ import annotations

import pytest

from brimyr.detect import (
    CoverageFormat,
    detect_ecosystems,
    ecosystem,
    for_repo,
    locate_coverage_file,
    locate_coverage_files,
)


def test_detect_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("def test_x(): pass\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["python"]
    assert found[0].coverage_format is CoverageFormat.COBERTURA


def test_detect_javascript(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["javascript"]
    assert found[0].coverage_format is CoverageFormat.LCOV


def test_detect_javascript_by_jest_config(tmp_path):
    # A jest/vitest config is a real test signal even without a test script.
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "jest.config.ts").write_text("export default {}\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["javascript"]


def test_bare_package_json_not_javascript(tmp_path):
    # A package.json with no test script / config — common for a backend that just
    # ships frontend assets — must NOT be detected as JS (no jest run on a red herring).
    (tmp_path / "package.json").write_text('{"dependencies": {"react": "^18"}}')
    assert detect_ecosystems(tmp_path) == []


def test_placeholder_test_script_not_javascript(tmp_path):
    # The `npm init` default placeholder is not a real test setup.
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "echo \\"Error: no test specified\\" && exit 1"}}'
    )
    assert detect_ecosystems(tmp_path) == []


def test_detect_dotnet_by_glob(tmp_path):
    (tmp_path / "App.csproj").write_text("<Project/>")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["dotnet"]


def test_detect_polyglot(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n[tool.pytest.ini_options]\ntestpaths = ['tests']\n"
    )
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    found = detect_ecosystems(tmp_path)
    assert {e.key for e in found} == {"python", "javascript"}


def test_detect_none(tmp_path):
    assert detect_ecosystems(tmp_path) == []


def test_ecosystem_lookup():
    assert ecosystem("python").key == "python"
    assert ecosystem("PYTHON").key == "python"
    assert ecosystem("nope") is None


def test_locate_coverage_exact(tmp_path):
    (tmp_path / "pyproject.toml").write_text("")
    (tmp_path / "coverage.xml").write_text("<coverage/>")
    eco = ecosystem("python")
    assert locate_coverage_file(eco, tmp_path).name == "coverage.xml"


def test_locate_coverage_glob(tmp_path):
    eco = ecosystem("dotnet")
    nested = tmp_path / "TestResults" / "guid-123"
    nested.mkdir(parents=True)
    (nested / "coverage.cobertura.xml").write_text("<coverage/>")
    found = locate_coverage_file(eco, tmp_path)
    assert found is not None
    assert found.name == "coverage.cobertura.xml"


def test_locate_coverage_missing(tmp_path):
    assert locate_coverage_file(ecosystem("python"), tmp_path) is None


def test_detect_java_maven(tmp_path):
    (tmp_path / "pom.xml").write_text("<project/>\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["java"]  # nosec B101
    assert found[0].coverage_format is CoverageFormat.JACOCO  # nosec B101


def test_gradle_alone_is_not_auto_detected(tmp_path):
    """`build.gradle` is a marker, but the built-in command is `mvn`.

    Auto-detecting here would run `mvn` in a Gradle repo, fail the run, and trip the
    broken-run rule into a red build. Gradle users pass `test_command` explicitly.
    """
    (tmp_path / "build.gradle").write_text("plugins { id 'java' }\n")
    assert detect_ecosystems(tmp_path) == []  # nosec B101
    # ...but forcing it by key still works, sharing the JaCoCo parser.
    assert ecosystem("java") is not None  # nosec B101


def test_java_coverage_files_span_every_reactor_module(tmp_path):
    """A multi-module build writes one report per module; all of them must be found."""
    for module in ("isam3d-case", "isam3d-user"):
        report = tmp_path / module / "target" / "site" / "jacoco"
        report.mkdir(parents=True)
        (report / "jacoco.xml").write_text("<report/>")
    found = locate_coverage_files(ecosystem("java"), tmp_path)
    assert [p.parts[-5] for p in found] == ["isam3d-case", "isam3d-user"]  # nosec B101


def test_vitest_repo_gets_the_vitest_command(tmp_path):
    """A vitest config is already a test signal — it must not be handed to jest."""
    (tmp_path / "package.json").write_text('{"scripts": {"test": "vitest run"}}')
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["javascript"]  # nosec B101
    assert "vitest" in found[0].command_str()  # nosec B101
    assert "jest" not in found[0].command_str()  # nosec B101
    # Same output file and format — only the binary differs.
    assert found[0].coverage_format is CoverageFormat.LCOV  # nosec B101
    assert found[0].coverage_paths == ("coverage/lcov.info",)  # nosec B101


def test_vitest_detected_from_dev_dependencies(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"scripts": {"test": "run-tests"}, "devDependencies": {"vitest": "^2"}}'
    )
    assert "vitest" in detect_ecosystems(tmp_path)[0].command_str()  # nosec B101


def test_jest_repo_is_untouched(tmp_path):
    (tmp_path / "package.json").write_text('{"scripts": {"test": "jest"}}')
    found = detect_ecosystems(tmp_path)
    assert "jest" in found[0].command_str()  # nosec B101
    assert "vitest" not in found[0].command_str()  # nosec B101


def test_vitest_does_not_double_match_a_polyglot_repo(tmp_path):
    """One JS run, not two — the variant replaces the entry, never adds a row."""
    (tmp_path / "pyproject.toml").write_text(
        "[project]\n[tool.pytest.ini_options]\ntestpaths = ['tests']\n"
    )
    (tmp_path / "package.json").write_text('{"devDependencies": {"vitest": "^2"}}')
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["python", "javascript"]  # nosec B101


def test_detect_dotnet_from_a_slnx_solution(tmp_path):
    """`.slnx` is the default solution format from .NET 10 onward.

    `dotnet new sln` writes `Foo.slnx`, and a repo scaffolded with current tooling has
    no `.sln` at all. Projects conventionally live under `src/`, and marker globbing only
    looks at the root, so the solution file is frequently the only marker present:
    without this, a whole modern solution is silently undetected and the run fails with
    "no ecosystem detected".
    """
    (tmp_path / "Demo.slnx").write_text("<Solution />\n")
    (tmp_path / "src" / "Core").mkdir(parents=True)
    (tmp_path / "src" / "Core" / "Core.csproj").write_text("<Project />\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["dotnet"]  # nosec B101


def test_dotnet_still_detected_from_a_classic_sln(tmp_path):
    (tmp_path / "Demo.sln").write_text("Microsoft Visual Studio Solution File\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["dotnet"]  # nosec B101


# ----------------------- python needs a real test signal -----------------------
# The guard that makes Brimyr provisionable fleet-wide instead of adopted repo by repo.
# `pyproject.toml` / `requirements.txt` / `tox.ini` are the most over-broad markers in
# the table: a docs build, a pre-commit pin list and a Terraform repo's tooling all ship
# one. Detecting python off the bare marker runs `pytest --cov`, which exits 5 with "no
# tests ran" and an empty report — which the broken-run rule then correctly reads as a
# tool error and turns red. Exactly the reasoning behind _js_has_test_signal and
# _java_is_maven; python was the one marker set that never got it.


def test_a_bare_pyproject_with_no_tests_is_not_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'docs'\n")
    (tmp_path / "requirements.txt").write_text("mkdocs\n")
    assert detect_ecosystems(tmp_path) == []


@pytest.mark.parametrize(
    ("name", "body"),
    [("pytest.ini", "[pytest]\n"), ("setup.cfg", "[tool:pytest]\n"), ("tox.ini", "[pytest]\n")],
)
def test_a_pytest_config_is_signal_enough(tmp_path, name, body):
    """A repo whose tests live somewhere the glob cannot reach still declares pytest."""
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / name).write_text(body)
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


def test_a_tox_ini_without_a_pytest_section_is_not_signal(tmp_path):
    # tox.ini is itself a python MARKER, so its presence proves nothing on its own —
    # the [pytest] section inside it is the signal, not the file.
    (tmp_path / "tox.ini").write_text("[tox]\nenvlist = py312\n")
    assert detect_ecosystems(tmp_path) == []


def test_a_vendored_test_file_is_not_this_repos_suite(tmp_path):
    """`brimyr local` runs against a working tree, where .venv and node_modules exist.

    Without the prune, one upstream `test_*.py` under site-packages makes every repo on
    the machine look tested.
    """
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    vendored = tmp_path / ".venv" / "Lib" / "site-packages" / "pkg"
    vendored.mkdir(parents=True)
    (vendored / "test_upstream.py").write_text("def test_x(): pass\n")
    assert detect_ecosystems(tmp_path) == []


def test_a_test_file_below_the_root_is_signal(tmp_path):
    # A backend/frontend split: the suite is nowhere near the marker that found it.
    (tmp_path / "requirements.txt").write_text("fastapi\n")
    nested = tmp_path / "backend" / "tests"
    nested.mkdir(parents=True)
    (nested / "test_api.py").write_text("def test_x(): pass\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


# --------------- ...but a NESTED PROJECT's tests are not this repo's ---------------
# One level deeper than the guard above. `broker/tests/` under a repo whose root is not
# a python project belongs to `broker`, whose dependencies live in ITS environment —
# pytest at the root collects those files and dies importing them, which is the empty
# report the broken-run rule turns red. Diatreme is the live case: bash + TypeScript,
# a root pyproject.toml holding nothing but [tool.semantic_release], all its python
# under broker/. Recursion is kept; only ownership is checked.


def _nested_project(tmp_path, directory: str = "broker", marker: str = "pyproject.toml"):
    (tmp_path / "pyproject.toml").write_text("[tool.semantic_release]\nversion = '1.0.0'\n")
    nested = tmp_path / directory
    (nested / "tests").mkdir(parents=True)
    (nested / marker).write_text("[project]\nname = 'broker'\n")
    (nested / "tests" / "test_token.py").write_text("def test_x(): pass\n")
    return tmp_path


def test_a_nested_projects_suite_is_not_this_repos_signal(tmp_path):
    assert detect_ecosystems(_nested_project(tmp_path)) == []


@pytest.mark.parametrize("marker", ["pyproject.toml", "setup.py", "setup.cfg"])
def test_any_project_marker_makes_a_subdirectory_its_own_project(tmp_path, marker):
    assert detect_ecosystems(_nested_project(tmp_path, marker=marker)) == []


def test_the_roots_own_marker_is_not_treated_as_nested(tmp_path):
    # Every python repo has a root marker; it is what makes these tests the root
    # project's in the first place. Counting it as "nested" would detect nothing, ever.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'app'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


def test_a_src_layout_with_root_tests_still_detects(tmp_path):
    # The layout the fix must not break: package under src/, suite at the root.
    (tmp_path / "requirements.txt").write_text("attrs\n")
    (tmp_path / "src" / "app").mkdir(parents=True)
    (tmp_path / "src" / "app" / "__init__.py").write_text("")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_app.py").write_text("def test_x(): pass\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


def test_the_roots_own_suite_wins_over_a_nested_project(tmp_path):
    # A repo that has both (chargate: tests/ at the root, plus a broker/ deployable)
    # is a python repo — the nested project only stops being *evidence*, it is not a veto.
    _nested_project(tmp_path)
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_cli.py").write_text("def test_x(): pass\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


def test_an_explicit_root_pytest_config_beats_the_ownership_check(tmp_path):
    # A repo that configures testpaths has SAID pytest runs from the root, whatever
    # the layout. The config short-circuit is checked first and must stay that way.
    _nested_project(tmp_path)
    (tmp_path / "pytest.ini").write_text("[pytest]\ntestpaths = broker/tests\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


def test_the_trailing_suffix_form_counts_too(tmp_path):
    (tmp_path / "setup.py").write_text("from setuptools import setup\n")
    (tmp_path / "thing_test.py").write_text("def test_x(): pass\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["python"]


# ─────────────────────────── shell / bats ───────────────────────────
#
# The marker set (`test`, `tests`, `*/tests`) is as over-broad as it gets on purpose:
# a bats suite is almost never at the repo root, and `_has_marker` globs the root only.
# Everything that keeps that safe is in `_shell_has_bats`, so that is what these test.


def _bats(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("@test 'it works' { run true; [ \"$status\" -eq 0 ]; }\n")


def test_detect_shell_by_a_bats_file(tmp_path):
    _bats(tmp_path / "tests" / "scan.bats")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["shell"]
    assert found[0].command_str() == "bats --recursive tests"


def test_a_tests_directory_alone_is_never_shell(tmp_path):
    """The `pyproject.toml` trap, refused up front.

    A marker with no confirming predicate detected python in every repo that shipped a
    packaging file, ran `pytest --cov` and turned it red. `tests/` is a far more common
    directory than `pyproject.toml` is a file, so a `tests/` marker without a strict
    predicate would be that bug with a wider blast radius: `bats` on a python repo.
    """
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_thing.py").write_text("def test_x(): pass\n")
    assert [e.key for e in detect_ecosystems(tmp_path)] == []


def test_a_vendored_bats_core_is_not_this_repos_suite(tmp_path):
    """`test/bats` is bats-core itself, and bats-core's checkout has its own `test/*.bats`.

    Detecting off it runs the FRAMEWORK's suite instead of the repo's — hundreds of
    tests that pass or fail for reasons the pull request has nothing to do with.
    """
    _bats(tmp_path / "test" / "bats" / "test" / "bats.bats")
    _bats(tmp_path / "test" / "test_helper" / "bats-assert" / "test" / "assert.bats")
    assert detect_ecosystems(tmp_path) == []


def test_a_vendored_framework_never_becomes_a_target(tmp_path):
    _bats(tmp_path / "test" / "scan.bats")
    _bats(tmp_path / "test" / "bats" / "test" / "bats.bats")
    (found,) = detect_ecosystems(tmp_path)
    assert found.command_str() == "bats --recursive test"


def test_bats_files_under_node_modules_are_not_a_suite(tmp_path):
    # `bats` is an npm package: installing it puts its own .bats files in the tree.
    _bats(tmp_path / "node_modules" / "bats" / "test" / "suite.bats")
    assert detect_ecosystems(tmp_path) == []


def test_nested_bats_directories_collapse_to_their_ancestor(tmp_path):
    """`--recursive` already descends, so passing both would run the same file twice."""
    _bats(tmp_path / "tests" / "top.bats")
    _bats(tmp_path / "tests" / "unit" / "deep.bats")
    (found,) = detect_ecosystems(tmp_path)
    assert found.command_str() == "bats --recursive tests"


def test_sibling_suites_are_both_run(tmp_path):
    _bats(tmp_path / "scripts" / "tests" / "lib.bats")
    _bats(tmp_path / "tests" / "cli.bats")
    (found,) = detect_ecosystems(tmp_path)
    assert found.command_str() == "bats --recursive scripts/tests tests"


def test_a_root_level_bats_file_is_passed_as_itself_not_as_dot(tmp_path):
    """`.` plus `--recursive` would walk node_modules, .venv and every vendored checkout."""
    _bats(tmp_path / "smoke.bats")
    (found,) = detect_ecosystems(tmp_path)
    assert found.command_str() == "bats --recursive smoke.bats"


def test_a_bats_suite_beside_a_dotnet_solution_detects_both(tmp_path):
    """The real case: a .NET repo whose certificate scanners are bash.

    One ecosystem winning would trade the other's coverage for it — which is exactly
    what forcing `test_command` did, and why `tests.yml` had to exist separately.
    """
    (tmp_path / "CertManagement.slnx").write_text("<Solution/>")
    _bats(tmp_path / "tests" / "scan.bats")
    assert [e.key for e in detect_ecosystems(tmp_path)] == ["dotnet", "shell"]


def test_a_forced_shell_ecosystem_is_tailored_to_the_repo(tmp_path):
    """Forcing is what a consumer does when detection misses their layout.

    Handing them the table's placeholder would run `bats --recursive tests` in a repo
    whose suite is somewhere else — a broken run caused by the escape hatch.
    """
    _bats(tmp_path / "scripts" / "tests" / "lib.bats")
    assert for_repo(ecosystem("shell"), tmp_path).command_str() == (
        "bats --recursive scripts/tests"
    )


def test_only_shell_declares_that_it_may_measure_nothing(tmp_path):
    """`coverage_optional` is the licence to pass with no report, so it stays rationed.

    Every other ecosystem instruments as it runs: a missing report there is a broken
    run, and one extra `True` in this table would turn a .NET project with no
    `coverlet.collector` from a red gate into a green one.
    """
    from brimyr.detect import ECOSYSTEMS

    assert [e.key for e in ECOSYSTEMS if e.coverage_optional] == ["shell"]
    assert ecosystem("shell").coverage_note


def test_forcing_javascript_still_means_jest(tmp_path):
    """`for_repo` must not start swapping binaries on the forced path.

    `--ecosystem vitest` already exists for asking, and this action is consumed
    fleet-wide on a pinned tag: a repo that forces `javascript` and runs jest today
    would suddenly be handed a different runner.
    """
    (tmp_path / "package.json").write_text('{"devDependencies": {"vitest": "^2"}}')
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    assert "jest" in for_repo(ecosystem("javascript"), tmp_path).command_str()
    assert "vitest" in detect_ecosystems(tmp_path)[0].command_str()


def test_a_discovered_path_reaches_the_shell_quoted(tmp_path):
    """The command runs under `shell=True`, and these paths are the only part of it
    nobody typed. Unquoted, a directory with a space arrives as two arguments and the
    run fails on a name."""
    _bats(tmp_path / "my tests" / "scan.bats")
    found = for_repo(ecosystem("shell"), tmp_path)
    assert found.command_str() == "bats --recursive 'my tests'"
