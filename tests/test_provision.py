"""Unit tests for making a detected ecosystem runnable (brimyr.provision)."""

from __future__ import annotations

from brimyr.detect import ecosystem
from brimyr.provision import plan

PY = ecosystem("python")
JS = ecosystem("javascript")
JAVA = ecosystem("java")
DOTNET = ecosystem("dotnet")


def _has(*names: str):
    """A `which` that reports exactly these binaries as installed."""
    present = set(names)
    return lambda name: f"/usr/bin/{name}" if name in present else None


_NOTHING = _has()


# ── Python ──────────────────────────────────────────────────────────────────


def test_uv_project_runs_through_uv_with_pytest_cov(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")

    result = plan(PY, tmp_path, which=_has("uv"))

    assert result.setup == ()
    assert result.command == f"uv run --with pytest-cov {PY.command_str()}"
    assert "uv.lock" in result.note


def test_pep621_project_without_a_lock_still_uses_uv(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    result = plan(PY, tmp_path, which=_has("uv"))

    assert result.command == f"uv run --with pytest-cov {PY.command_str()}"


def test_pytest_cov_is_injected_because_the_repo_never_declares_it(tmp_path):
    """The plugin `--cov` needs is brimyr's requirement, not the repo's.

    A repo can have a complete, working pytest setup and still have no pytest-cov
    anywhere, which is exactly grimoire's shape: `uv sync` alone would leave
    `pytest --cov` failing on an unrecognised argument.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname = 'x'\n[dependency-groups]\ndev = ['pytest>=8']\n"
    )

    assert "--with pytest-cov" in plan(PY, tmp_path, which=_has("uv")).command


def test_requirements_only_repo_gets_an_ephemeral_environment(tmp_path):
    (tmp_path / "requirements.txt").write_text("requests\n")

    result = plan(PY, tmp_path, which=_has("uv"))

    assert result.command == (
        f"uv run --with pytest-cov --with-requirements requirements.txt {PY.command_str()}"
    )


def test_dev_requirements_win_over_the_base_file(tmp_path):
    """The dev file is the superset that names the test dependencies."""
    (tmp_path / "requirements.txt").write_text("requests\n")
    (tmp_path / "requirements-dev.txt").write_text("-r requirements.txt\npytest\n")

    assert "requirements-dev.txt" in plan(PY, tmp_path, which=_has("uv")).command


def test_poetry_1x_layout_uses_poetry_not_uv(tmp_path):
    """No `[project]` table means uv would install none of its dependencies."""
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'x'\n")

    result = plan(PY, tmp_path, which=_has("uv", "poetry"))

    assert result.setup[0] == "poetry install --no-interaction --no-ansi"
    assert "pytest-cov" in result.setup[1]
    assert result.command == f"poetry run {PY.command_str()}"


def test_pytest_cov_in_a_poetry_group_is_not_reinstalled(tmp_path):
    """The only cover for `_poetry_declares`' named-group traversal — keep the group.

    Rewriting this fixture to a flat `[tool.poetry.dependencies]` would still pass while
    leaving `[tool.poetry.group.*.dependencies]` completely unguarded.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'x'\n[tool.poetry.group.dev.dependencies]\npytest-cov = '*'\n"
    )

    assert plan(PY, tmp_path, which=_has("poetry")).setup == (
        "poetry install --no-interaction --no-ansi",
    )


def test_a_comment_mentioning_pytest_cov_does_not_suppress_the_inject(tmp_path):
    """The check is a TOML table lookup, not a substring scan over the file.

    A grep reads this comment as a declared dependency and skips the install the
    comment is literally asking for, and `poetry run pytest --cov` then dies on
    `unrecognized arguments` — the broken run this module exists to prevent.
    """
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'x'\n"
        "[tool.poetry.dependencies]\n"
        "# note: pytest-cov is needed before --cov will work\n"
        "requests = '*'\n"
    )

    setup = plan(PY, tmp_path, which=_has("poetry")).setup

    assert len(setup) == 2
    assert "pytest-cov" in setup[1]


def test_pytest_cov_in_the_main_dependency_table_counts(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'x'\n[tool.poetry.dependencies]\npytest-cov = '^5'\n"
    )

    assert len(plan(PY, tmp_path, which=_has("poetry")).setup) == 1


def test_the_legacy_dev_dependencies_table_counts_too(tmp_path):
    """Poetry moved the goalposts twice; `dev-dependencies` predates groups."""
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname = 'x'\n[tool.poetry.dev-dependencies]\npytest-cov = '*'\n"
    )

    assert len(plan(PY, tmp_path, which=_has("poetry")).setup) == 1


def test_a_scalar_tool_poetry_falls_back_to_injecting(tmp_path):
    """Valid TOML, nonsense shape. Injecting is the safe answer; raising is not."""
    (tmp_path / "pyproject.toml").write_text("[tool]\npoetry = 'yes'\n")

    setup = plan(PY, tmp_path, which=_has("poetry")).setup

    assert len(setup) == 2
    assert "pytest-cov" in setup[1]


def test_an_unparseable_pyproject_degrades_instead_of_raising(tmp_path):
    """A pyproject brimyr cannot read is the repo's bug, not a reason to crash its gate."""
    (tmp_path / "pyproject.toml").write_text("[project\nthis is not toml")

    result = plan(PY, tmp_path, which=_has("uv", "poetry"))

    assert result.setup == ()
    assert result.command is None


def test_an_unparseable_pyproject_still_uses_a_uv_lock(tmp_path):
    """The lockfile is its own evidence of a uv project; the broken table is not needed."""
    (tmp_path / "pyproject.toml").write_text("[project\nthis is not toml")
    (tmp_path / "uv.lock").write_text("version = 1\n")

    assert plan(PY, tmp_path, which=_has("uv")).command.startswith("uv run")


def test_a_poetry_repo_without_poetry_declines_and_names_the_tool(tmp_path):
    """Never fall through to uv here: it reads a 1.x layout as having no dependencies."""
    (tmp_path / "pyproject.toml").write_text("[tool.poetry]\nname = 'x'\n")

    result = plan(PY, tmp_path, which=_has("uv"))

    assert result.setup == ()
    assert result.command is None
    assert "poetry" in result.note


def test_poetry_2x_writes_a_project_table_and_goes_down_the_uv_path(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n[tool.poetry]\n")

    assert plan(PY, tmp_path, which=_has("uv", "poetry")).command.startswith("uv run")


def test_no_uv_means_no_provisioning_and_a_reason(tmp_path):
    """Declining is fine; declining silently is what made this bug invisible."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    result = plan(PY, tmp_path, which=_NOTHING)

    assert result.setup == ()
    assert result.command is None
    assert "uv" in result.note


def test_a_directory_with_no_python_project_is_left_completely_alone(tmp_path):
    result = plan(PY, tmp_path, which=_has("uv"))

    assert result.setup == ()
    assert result.command is None


# ── JavaScript ──────────────────────────────────────────────────────────────


def test_missing_node_modules_are_installed(tmp_path):
    (tmp_path / "package.json").write_text("{}")

    result = plan(JS, tmp_path, which=_has("npm"))

    assert result.setup == ("npm ci --no-audit --no-fund || npm install --no-audit --no-fund",)
    assert result.command is None


def test_existing_node_modules_are_not_reinstalled(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "node_modules").mkdir()

    assert plan(JS, tmp_path, which=_has("npm")).setup == ()


def test_no_npm_means_no_install(tmp_path):
    (tmp_path / "package.json").write_text("{}")

    result = plan(JS, tmp_path, which=_NOTHING)

    assert result.setup == ()
    assert "npm" in result.note


# ── The ecosystems that restore themselves ──────────────────────────────────


def test_maven_and_dotnet_plan_to_nothing(tmp_path):
    for eco in (JAVA, DOTNET):
        result = plan(eco, tmp_path, which=_has("uv", "npm", "poetry"))
        assert result.setup == ()
        assert result.command is None


def test_the_wrapped_command_is_the_one_passed_in(tmp_path):
    """`plan` wraps whatever it is handed, so detect.py stays the single source."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")

    result = plan(PY, tmp_path, command="pytest -q --cov", which=_has("uv"))

    assert result.command == "uv run --with pytest-cov pytest -q --cov"
