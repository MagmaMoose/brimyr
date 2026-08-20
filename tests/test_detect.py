"""Unit tests for ecosystem detection (brimyr.detect)."""

from __future__ import annotations

from brimyr.detect import (
    CoverageFormat,
    detect_ecosystems,
    ecosystem,
    locate_coverage_file,
    locate_coverage_files,
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
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "package.json").write_text('{"devDependencies": {"vitest": "^2"}}')
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    found = detect_ecosystems(tmp_path)
    assert [e.key for e in found] == ["python", "javascript"]  # nosec B101
