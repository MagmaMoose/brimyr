"""Unit tests for ecosystem detection (brimyr.detect)."""

from __future__ import annotations

from brimyr.detect import (
    CoverageFormat,
    detect_ecosystems,
    ecosystem,
    locate_coverage_file,
    python_env_manager,
    resolve_command,
)


def test_detect_python(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\n")
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
    (tmp_path / "pyproject.toml").write_text("[project]\n")
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


# ── managed Python environments ────────────────────────────────────────────
# A uv project's pytest lives in the project virtualenv, never on PATH. Brimyr
# shelling out to a bare `pytest` produced "command not found", which its own
# broken-run rule then reported as a tool error — correct, but the cause looked
# like a brimyr bug rather than a missing `uv run`. Every uv-managed repo in the
# org failed this way from the day the gate was provisioned.


def _py(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
    return ecosystem("python")


def test_a_plain_python_repo_runs_pytest_directly(tmp_path):
    assert python_env_manager(tmp_path) == ()
    assert resolve_command(_py(tmp_path), tmp_path).startswith("pytest --cov")


def test_a_uv_project_runs_pytest_through_uv(tmp_path):
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    assert python_env_manager(tmp_path) == ("uv", "run")
    assert resolve_command(_py(tmp_path), tmp_path) == (
        "uv run pytest --cov --cov-report=xml --cov-report=term-missing"
    )


def test_the_uv_prefix_only_wraps_it_never_rewrites_the_command(tmp_path):
    # The coverage flags are what make the run usable; a prefix must not disturb them.
    eco = _py(tmp_path)
    plain = resolve_command(eco, tmp_path)
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    assert resolve_command(eco, tmp_path) == f"uv run {plain}"


def test_a_uv_lock_does_not_touch_javascript_or_dotnet(tmp_path):
    # uv manages Python environments only. Prefixing `npx jest` or `dotnet test`
    # with `uv run` would break a polyglot repo that happens to have a uv.lock.
    (tmp_path / "uv.lock").write_text("version = 1\n", encoding="utf-8")
    for key in ("javascript", "dotnet"):
        eco = ecosystem(key)
        assert resolve_command(eco, tmp_path) == eco.command_str()


def test_a_uv_lock_that_is_a_directory_is_not_a_uv_project(tmp_path):
    (tmp_path / "uv.lock").mkdir()
    assert python_env_manager(tmp_path) == ()
