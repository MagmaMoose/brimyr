"""Unit tests for making a detected ecosystem runnable (brimyr.provision)."""

from __future__ import annotations

import pytest

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

    # `--no-project`: from a nested directory uv would otherwise walk UP to whatever
    # pyproject.toml sits above it and sync that project instead. `python -m` puts the
    # project directory on sys.path, as the `pip install -r` job it reproduces does.
    assert result.command == (
        "uv run --no-project --with pytest-cov --with pip "
        f"--with-requirements requirements.txt python -m {PY.command_str()}"
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


# ── Shell: bats is provisioned, kcov is not ─────────────────────────────────


SHELL = ecosystem("shell")


def test_bats_on_path_runs_as_is(tmp_path):
    result = plan(SHELL, tmp_path, which=_has("bats"))

    assert result.setup == ()
    assert result.command == SHELL.command_str()
    assert "kcov" in result.note


def test_bats_is_fetched_through_npx_when_it_is_not_installed(tmp_path):
    """A shell `127` is a broken run and a red gate on a repo whose tests are fine.

    Shell is auto-detected fleet-wide off a `.bats` file, and nothing installs bats on
    a hosted runner, so without this every repo that owns a bats suite would go red the
    day it was detected. Same closure as `npx --yes jest`.
    """
    result = plan(SHELL, tmp_path, which=_has("npx"))

    assert result.command == f"npx --yes {SHELL.command_str()}"
    assert result.setup == ()


def test_kcov_is_used_when_present_and_writes_where_detection_looks(tmp_path):
    """The wrap and `Ecosystem.coverage_paths` are one contract in two files.

    A wrap that writes somewhere else produces a green, silent, permanently unmeasured
    shell half — the report is there and nothing ever finds it.
    """
    result = plan(SHELL, tmp_path, which=_has("bats", "kcov"))

    assert result.command.startswith("kcov ")
    assert result.command.endswith(SHELL.command_str())
    out = result.command.split()[3]
    assert any(pattern.startswith(f"{out}/") for pattern in SHELL.coverage_paths)


def test_kcov_is_never_installed(tmp_path):
    """No setup command, ever: kcov is a system package, not a repo-owned dependency.

    Installing it would mean `apt-get` into the caller's runner image for every shell
    repo on the estate, or minutes of source build per job. Its absence is why `shell`
    is `coverage_optional` — the gap is designed for, not worked around.
    """
    for which in (_has("bats"), _has("bats", "kcov"), _has("npx"), _NOTHING):
        assert plan(SHELL, tmp_path, which=which).setup == ()


def test_no_bats_and_no_npx_declines_and_says_why(tmp_path):
    result = plan(SHELL, tmp_path, which=_NOTHING)

    assert result.command is None
    assert "bats" in result.note and "npx" in result.note


# ── a pinned requirements file is the lock ──────────────────────────────────
#
# MagmaMoose/dunmir's backend is the shape: a `[project]` table that declares
# `fastapi` unpinned, a requirements.txt that pins `fastapi==0.115.6` (newer releases
# mount one route of 24) plus `pynacl`, which the table omits, and a `readme` outside
# the project that stops it being built at all. Its CI runs `pip install -r` and
# pytest from the project directory; building the project instead cannot even start.

_PINNED = "fastapi==0.115.6\npyjwt[crypto]==2.13.0\npynacl==1.6.2 ; python_version >= '3.8'\n"


def test_a_project_whose_requirements_pin_everything_is_run_from_them(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\ndependencies = ['fastapi']\n")
    (tmp_path / "requirements.txt").write_text(_PINNED)

    result = plan(PY, tmp_path, which=_has("uv"))

    assert result.command == (
        "uv run --no-project --with pytest-cov --with pip "
        f"--with-requirements requirements.txt python -m {PY.command_str()}"
    )
    assert "pinned" in result.note


def test_unpinned_requirements_leave_a_project_synced_as_before(tmp_path):
    # Not a lock, so not the environment: nothing changes for a repo that has a verdict.
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "requirements-dev.txt").write_text("pytest>=8\nhypothesis\n")

    assert (
        plan(PY, tmp_path, which=_has("uv")).command
        == f"uv run --with pytest-cov {PY.command_str()}"
    )


def test_a_pinned_file_of_something_else_is_not_the_projects_lock(tmp_path):
    # Every line pinned, but it is docs tooling: the project's own `requests` is nowhere
    # in it. Running from it would install mkdocs and none of what the tests import.
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\ndependencies=['requests']\n")
    (tmp_path / "requirements.txt").write_text("mkdocs==1.6.0\n")

    assert plan(PY, tmp_path, which=_has("uv")).command == (
        f"uv run --with pytest-cov {PY.command_str()}"
    )


def test_a_uv_lock_still_wins_over_a_pinned_requirements_file(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "uv.lock").write_text("version = 1\n")
    (tmp_path / "requirements.txt").write_text(_PINNED)

    assert plan(PY, tmp_path, which=_has("uv")).command.startswith("uv run --with pytest-cov")


def test_a_src_layout_is_installed_because_nothing_else_makes_it_importable(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "requirements.txt").write_text(_PINNED)
    (tmp_path / "src" / "x").mkdir(parents=True)

    assert "--with-editable ." in plan(PY, tmp_path, which=_has("uv")).command


def test_pinning_is_read_through_includes_and_continuation_lines(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname = 'x'\n")
    (tmp_path / "base.txt").write_text(
        "-r requirements-dev.txt\nfastapi==0.115.6 \\\n    --hash=sha256:abc\n"
    )
    (tmp_path / "requirements-dev.txt").write_text("-r base.txt\n# a comment\npytest==8.3.0\n")

    # A cycle of includes ends where it started, and every requirement it saw is pinned.
    assert "--no-project" in plan(PY, tmp_path, which=_has("uv")).command

    (tmp_path / "base.txt").write_text("-r requirements-dev.txt\nfastapi\n")
    assert "--no-project" not in plan(PY, tmp_path, which=_has("uv")).command


# ── plugins the pytest configuration names ──────────────────────────────────


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("pytest.ini", "[pytest]\nasyncio_mode = auto\n"),
        ("setup.cfg", "[tool:pytest]\nasyncio_mode = auto\n"),
        ("pyproject.toml", "[project]\nname='x'\n[tool.pytest.ini_options]\nasyncio_mode='auto'\n"),
    ],
)
def test_an_asyncio_mode_nobody_declared_gets_pytest_asyncio(tmp_path, name, body):
    # Without the plugin pytest only warns about the setting, then fails every async test.
    (tmp_path / name).write_text(body)
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.6\n")

    assert "--with pytest-asyncio" in plan(PY, tmp_path, which=_has("uv")).command


def test_a_declared_pytest_asyncio_keeps_the_repos_own_version(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\nasyncio_mode = auto\n")
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.6\nPytest_Asyncio==0.24.0\n")

    assert "pytest-asyncio" not in plan(PY, tmp_path, which=_has("uv")).command


def test_a_setting_in_a_comment_is_not_a_setting(tmp_path):
    (tmp_path / "pytest.ini").write_text("[pytest]\n# asyncio_mode = auto\n")
    (tmp_path / "requirements.txt").write_text("fastapi==0.115.6\n")

    assert "pytest-asyncio" not in plan(PY, tmp_path, which=_has("uv")).command


def test_a_poetry_repo_gets_the_plugin_in_its_own_environment(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[tool.poetry]\nname='x'\n[tool.pytest.ini_options]\nasyncio_mode='auto'\n"
    )

    setup = plan(PY, tmp_path, which=_has("poetry")).setup

    assert setup[-1].startswith("poetry run python -m pip install")
    assert setup[-1].endswith("pytest-cov pytest-asyncio")


# ── conventional test extras and groups ─────────────────────────────────────


def test_a_dev_extra_is_synced_and_what_it_declares_is_not_injected(tmp_path):
    # brimyr's own broker: pytest-asyncio lives in `[project.optional-dependencies] dev`,
    # which `uv run` installs only when asked.
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n"
        "[project.optional-dependencies]\ndev=['pytest-asyncio>=0.24']\nserver=['uvicorn']\n"
        "[tool.pytest.ini_options]\nasyncio_mode='auto'\n"
    )
    (tmp_path / "uv.lock").write_text("version = 1\n")

    command = plan(PY, tmp_path, which=_has("uv")).command

    assert command == f"uv run --extra dev --with pytest-cov {PY.command_str()}"


def test_a_test_group_is_synced_beside_the_default_dev_group(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[dependency-groups]\ntest=['pytest']\ndocs=['mkdocs']\n"
    )

    assert "--group test --with pytest-cov" in plan(PY, tmp_path, which=_has("uv")).command


# ── JavaScript: vitest needs a coverage provider ────────────────────────────

VITEST = ecosystem("vitest")


def test_a_vitest_suite_with_no_coverage_provider_gets_one_at_vitests_version(tmp_path):
    (tmp_path / "package.json").write_text('{"devDependencies": {"vitest": "^4"}}')

    setup = plan(VITEST, tmp_path, which=_has("npm")).setup

    assert setup[0].startswith("npm ci")
    assert "@vitest/coverage-v8@$(node -p" in setup[1]
    assert "--no-save" in setup[1]


@pytest.mark.parametrize("provider", ["@vitest/coverage-v8", "@vitest/coverage-istanbul"])
def test_a_declared_provider_is_left_alone(tmp_path, provider):
    (tmp_path / "package.json").write_text(f'{{"devDependencies": {{"{provider}": "^4"}}}}')

    assert len(plan(VITEST, tmp_path, which=_has("npm")).setup) == 1


def test_an_installed_provider_is_left_alone_even_when_undeclared(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "node_modules" / "@vitest" / "coverage-v8").mkdir(parents=True)

    assert plan(VITEST, tmp_path, which=_has("npm")).setup == ()


def test_jest_needs_no_provider(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "node_modules").mkdir()

    assert plan(JS, tmp_path, which=_has("npm")).setup == ()


# ── Go ───────────────────────────────────────────────────────────────────────


def test_go_restores_its_own_modules_and_says_when_go_is_missing(tmp_path):
    go = ecosystem("go")
    present = plan(go, tmp_path, which=_has("go"))
    assert (present.setup, present.command) == ((), None)
    assert "`go` is not on PATH" in plan(go, tmp_path, which=_NOTHING).note
