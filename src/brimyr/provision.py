"""Make a detected ecosystem *runnable* before its tests are run.

Detection answers "what language is this?". It does not answer "can ``pytest``
even be launched here?", and for a long time Brimyr only asked the first
question: it detected Python off a real pytest config, shelled out to
``pytest --cov``, and on a runner where nothing had installed the repo's
dependencies got ``/bin/sh: 1: pytest: not found``, no coverage file, and a
BROKEN run. A red gate, on a repo whose tests are fine.

That failure is fatal to the point of the gate. Brimyr is provisioned
fleet-wide from one org workflow that knows nothing about any individual repo's
toolchain, so "install your test dependencies in a step before ours" is a
contract no fleet-wide workflow can honour — and every repo that has not
hand-written its own workflow goes red on an environment problem that has
nothing to do with its coverage. Provisioning closes that gap using the markers
detection has already read: work out how this repo installs its own
dependencies, and use it.

Two shapes come out of :func:`plan`:

* a **setup command** run before the tests (``npm ci`` — the JS equivalent of
  the same bug: ``npx --yes jest`` downloads jest and then cannot import a
  single one of the repo's own modules), and
* a **replacement test command** that wraps the detected one in the repo's own
  dependency manager (``uv run …`` / ``poetry run …``), so the suite runs
  against the environment that repo actually declares.

The rules are deliberately conservative. Provisioning only ever happens when
the repo shows a project shape it can act on *and* the tool that owns it is
installed; anything else returns an empty plan with a ``note`` saying why, and
the run proceeds exactly as it did before. Nothing here installs into the
ambient interpreter — a gate has no business mutating the environment its
caller's other steps are using, and ``brimyr local`` has no business mutating a
developer's.

This module reads the filesystem and asks whether a binary exists. It runs
nothing: :mod:`brimyr.runner` owns the subprocess boundary, and ``which`` is
injected so the whole decision table is unit-tested without a toolchain.
"""

from __future__ import annotations

import configparser
import json
import re
import shutil
import tomllib
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from brimyr.detect import Ecosystem

#: Probe for an executable. Injected so tests need no real toolchain.
Which = Callable[[str], str | None]

#: Requirements files, most specific first. A repo with both a dev and a base file
#: needs the dev one — it is the superset that names the test dependencies — so the
#: first match wins rather than all of them being installed.
_REQUIREMENTS = (
    "requirements-dev.txt",
    "requirements_dev.txt",
    "requirements-test.txt",
    "requirements_test.txt",
    "requirements.txt",
)

#: Optional-dependency extras that hold a project's test dependencies by convention.
#: `uv run` installs none of them unless asked, so a repo whose pytest-asyncio lives in
#: `[project.optional-dependencies] dev` (brimyr's own broker) got a suite with its async
#: tests failing. Only extras the project actually defines are passed; `uv` refuses an
#: unknown one.
_TEST_EXTRAS = ("test", "tests", "testing", "dev")

#: PEP 735 groups that do. `dev` is absent on purpose: uv already syncs it by default.
_TEST_GROUPS = ("test", "tests", "testing")

#: pytest settings owned by a plugin, and that plugin. Without it pytest only WARNS about
#: an unknown setting, and then fails every `async def` test, because nothing runs them.
#: MagmaMoose/dunmir's backend sets `asyncio_mode = auto` and declares pytest-asyncio
#: nowhere: its CI installs the plugin by hand, which no fleet-wide workflow can know.
_PLUGIN_SETTINGS = (
    ("asyncio_mode", "pytest-asyncio"),
    ("asyncio_default_fixture_loop_scope", "pytest-asyncio"),
)

#: Where pytest reads its settings from: file, and the section that makes it a config.
_PYTEST_INI_SECTIONS = (
    ("pytest.ini", "pytest"),
    (".pytest.ini", "pytest"),
    ("tox.ini", "pytest"),
    ("setup.cfg", "tool:pytest"),
)

#: The package name at the start of a requirement: `pyjwt[crypto]==2.13.0` -> `pyjwt`.
_REQUIREMENT_NAME = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)")


@dataclass(frozen=True)
class Provision:
    """How to make one ecosystem's tests runnable in this checkout.

    An empty plan (no ``setup``, no ``command``) means "run exactly what detection
    chose", which is the correct answer whenever the repo is already provisioned or
    Brimyr has no safe way to provision it. ``note`` is diagnostic in both cases: on
    an empty plan it says why nothing was done, which is the line that turns a
    mystifying ``pytest: not found`` into an actionable one.
    """

    #: Shell commands to run, in order, before the tests. A non-zero exit from any of
    #: them is a broken run — a dependency install that failed has not left an
    #: environment whose coverage number means anything.
    setup: tuple[str, ...] = ()
    #: Replaces the ecosystem's detected test command when set.
    command: str | None = None
    #: One line, for the log. Always set; never load-bearing.
    note: str = ""


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _first_existing(root: Path, names: tuple[str, ...]) -> str | None:
    for name in names:
        if (root / name).is_file():
            return name
    return None


def _parse_pyproject(root: Path) -> dict:
    """``pyproject.toml`` as data, or ``{}`` when there isn't one that parses.

    Read as TOML rather than grepped, because every question below is about a *table*
    and a substring answers a different question: `# note: pytest-cov is needed` in a
    comment reads as a declared dependency, and the injection that comment was asking
    for is then skipped. `pytest --cov` dies on `unrecognized arguments`, which is the
    exact broken run this module exists to prevent (brimyr#56).

    `tomllib` is stdlib on 3.11+, so this costs no dependency. A malformed file falls
    back to ``{}`` and the caller degrades to running the tests as-is: a pyproject
    brimyr cannot parse is the repo's problem to fix, not a reason to fail its gate.
    """
    text = _read(root / "pyproject.toml")
    if not text:
        return {}
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        return {}


def _poetry_declares(data: dict, package: str) -> bool:
    """True if a pre-2.0 Poetry project declares ``package`` anywhere it can.

    Four places, because Poetry moved the goalposts twice: the main table, the legacy
    `dev-dependencies`, and any number of named groups under `group.<name>`.
    """
    poetry = data.get("tool", {}).get("poetry", {})
    if not isinstance(poetry, dict):
        return False
    tables = [poetry.get("dependencies"), poetry.get("dev-dependencies")]
    groups = poetry.get("group")
    if isinstance(groups, dict):
        tables.extend(g.get("dependencies") for g in groups.values() if isinstance(g, dict))
    return any(package in t for t in tables if isinstance(t, dict))


def _canonical(name: str) -> str:
    """PEP 503 normalisation: `Pytest_Asyncio` and `pytest-asyncio` are one package."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _names(requirements: Iterable[object]) -> set[str]:
    """Canonical package names out of PEP 508 strings; anything else is skipped."""
    names: set[str] = set()
    for requirement in requirements:
        if isinstance(requirement, str) and "://" not in requirement.split("@")[0]:
            match = _REQUIREMENT_NAME.match(requirement.strip())
            if match:
                names.add(_canonical(match.group(1)))
    return names


def _requirement_lines(path: Path, seen: frozenset[Path] = frozenset()) -> list[str]:
    """The requirement lines of a requirements file, `-r` includes followed.

    Options are dropped (`-e`, `-c`, `--index-url`, a `--hash` continuation): they
    install nothing by name, and a constraints file installs nothing at all. A cycle of
    `-r` includes ends where it started instead of recursing for ever.
    """
    resolved = path.resolve()
    if resolved in seen:
        return []
    lines: list[str] = []
    # A trailing backslash continues the line; hashes are usually written that way.
    for raw in re.sub(r"\\\r?\n", " ", _read(path)).splitlines():
        line = re.sub(r"(^|\s)#.*$", "", raw).strip()
        include = re.match(r"^(?:-r|--requirement)(?:\s+|=)(\S+)$", line)
        if include:
            lines.extend(_requirement_lines(path.parent / include.group(1), seen | {resolved}))
        elif line and not line.startswith("-"):
            lines.append(line)
    return lines


def _pinned(line: str) -> bool:
    """True when a requirement names exactly one version (`==`/`===`) or one artifact."""
    spec = line.split(";", 1)[0]
    return "==" in spec or " @ " in spec


def _pytest_settings(root: Path, data: dict) -> set[str]:
    """Every setting name the repo's pytest configuration uses, wherever it lives.

    Parsed, not grepped, for the reason `_parse_pyproject` gives: `asyncio_mode` in a
    comment is not a setting. pytest reads the first config file it finds and ignores
    the rest, but a union is the safe side here: the answer only ever adds a plugin.
    """
    settings: set[str] = set()
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    table = tool.get("pytest")
    if isinstance(table, dict):
        ini = table.get("ini_options")
        if isinstance(ini, dict):
            settings.update(ini)
        # pytest 9 reads a native `[tool.pytest]` table too, beside the ini_options one.
        settings.update(key for key in table if key != "ini_options")
    for name, section in _PYTEST_INI_SECTIONS:
        text = _read(root / name)
        if not text:
            continue
        parser = configparser.ConfigParser(interpolation=None, strict=False)
        try:
            parser.read_string(text)
        except configparser.Error:
            continue
        if parser.has_section(section):
            settings.update(parser.options(section))
    return settings


def _plugins_needed(root: Path, data: dict) -> list[str]:
    """The pytest plugins this repo's configuration depends on, in a stable order."""
    settings = _pytest_settings(root, data)
    return list(dict.fromkeys(plugin for key, plugin in _PLUGIN_SETTINGS if key in settings))


def _project_declares(data: dict, extras: list[str], groups: list[str]) -> set[str]:
    """Package names a `uv run` with these extras and groups will install.

    Only what is actually synced counts: a plugin declared in an extra that is not
    being installed is, for this run, not declared at all.
    """
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    declared = _names(project.get("dependencies") or [])
    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra in extras:
            declared |= _names(optional.get(extra) or [])
    dependency_groups = data.get("dependency-groups")
    if isinstance(dependency_groups, dict):
        for group in ("dev", *groups):
            declared |= _names(dependency_groups.get(group) or [])
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    uv = tool.get("uv", {}) if isinstance(tool.get("uv"), dict) else {}
    return declared | _names(uv.get("dev-dependencies") or [])


def _defined(data: dict, table: str, names: tuple[str, ...]) -> list[str]:
    """Which of ``names`` the project defines under ``table`` (an extra or a group)."""
    if table == "optional-dependencies":
        project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
        defined = project.get("optional-dependencies")
    else:
        defined = data.get(table)
    return [name for name in names if isinstance(defined, dict) and name in defined]


def _python_plan(root: Path, command: str, which: Which) -> Provision:
    """How this Python repo installs itself, and how to run pytest inside that.

    ``uv`` first, and for one reason: it is the only tool here that installs the
    project, its PEP 735 dev dependency group *and* a test-only package that the repo
    does not declare, in a single command, from ``uv.lock`` when there is one. That
    last part matters more than it looks — ``pytest-cov`` is a Brimyr requirement, not
    the repo's, so a repo can have a perfectly good pytest setup and still have no way
    to satisfy ``--cov``. ``--with pytest-cov`` supplies it without touching the
    repo's own declared dependencies, and because ``pytest-cov`` depends on ``pytest``
    it also covers a repo that has test files but never declared the runner. A plugin
    the repo's pytest configuration names but never declares (``asyncio_mode`` without
    pytest-asyncio) is injected the same way; one it does declare keeps its version.

    Which environment, in order:

    * ``uv.lock`` is the lock, so the project is synced from it, with any conventional
      test extras (``test``/``tests``/``testing``/``dev``) and groups it defines.
    * A requirements file that pins EVERY requirement (``==``) and names every dependency
      the project declares is a lock too, and when there is no ``uv.lock`` it is the
      only one. The suite runs in an environment built
      from it, from the project directory (``python -m pytest``), exactly as the repo's
      own ``pip install -r`` job runs it. Building the project instead resolved its
      ``[project]`` table afresh: MagmaMoose/dunmir's backend declares ``fastapi``
      unpinned there and pins ``0.115.6`` in ``requirements.txt`` because newer
      releases mount one route of 24, lists ``pynacl`` only in the requirements file,
      and cannot be built at all (its ``readme`` sits outside the project).
    * Any other ``[project]`` or ``[tool.uv]`` project is synced as a project.
    * A requirements file alone gets the same ephemeral environment as a pinned one.

    The requirements environment carries ``pip`` too: it reproduces a ``pip install
    -r`` job, whose interpreter has pip, and suites that shell out to ``python -m pip``
    (dunmir's Lambda packaging tests) fail without it. A uv-native repo's own CI has no
    pip either, so its tests already cope and it gets none.

    Poetry gets its own branch only for the pre-2.0 layout, where dependencies live
    under ``[tool.poetry.dependencies]`` and there is no ``[project]`` table at all —
    uv reads that repo as having no dependencies and would install none of them.
    Poetry 2.x writes a standard ``[project]`` table and goes down the uv path like
    everything else.

    Every question here is asked of parsed TOML, not of the file's text: see
    :func:`_parse_pyproject` for why a substring answers a different question.
    """
    data = _parse_pyproject(root)
    tool = data.get("tool", {}) if isinstance(data.get("tool"), dict) else {}
    has_pep621 = "project" in data
    poetry_only = "poetry" in tool and not has_pep621
    plugins = _plugins_needed(root, data)

    if poetry_only:
        if not which("poetry"):
            return Provision(
                note="poetry project, but `poetry` is not on PATH — running tests as-is"
            )
        setup = ["poetry install --no-interaction --no-ansi"]
        # Into poetry's OWN virtualenv, never the ambient interpreter. Skipped for a
        # plugin the repo already declares, so a project that pins a version keeps it.
        missing = [p for p in ("pytest-cov", *plugins) if not _poetry_declares(data, p)]
        if missing:
            setup.append(
                "poetry run python -m pip install --disable-pip-version-check --quiet "
                + " ".join(missing)
            )
        return Provision(
            setup=tuple(setup),
            command=f"poetry run {command}",
            note="poetry install + poetry run",
        )

    if not which("uv"):
        return Provision(note="`uv` is not on PATH — running tests as-is")

    locked = (root / "uv.lock").is_file()
    requirements = _first_existing(root, _REQUIREMENTS)
    lines = _requirement_lines(root / requirements) if requirements else []
    # A lock OF THIS PROJECT: every line pinned, and every dependency the project declares
    # among them. A pinned file of something else (docs tooling) is not its environment.
    project = data.get("project", {}) if isinstance(data.get("project"), dict) else {}
    declared_by_project = _names(project.get("dependencies") or [])
    pinned = (
        bool(lines)
        and all(_pinned(line) for line in lines)
        and declared_by_project <= _names(lines)
    )

    if locked or ((has_pep621 or "uv" in tool) and not pinned):
        extras = _defined(data, "optional-dependencies", _TEST_EXTRAS)
        groups = _defined(data, "dependency-groups", _TEST_GROUPS)
        declared = _project_declares(data, extras, groups)
        injected = ["pytest-cov", *(p for p in plugins if _canonical(p) not in declared)]
        flags = [
            *(f"--extra {extra}" for extra in extras),
            *(f"--group {group}" for group in groups),
            *(f"--with {package}" for package in injected),
        ]
        synced = " + ".join(
            [
                "project and dev group",
                *(f"extra {e}" for e in extras),
                *(f"group {g}" for g in groups),
            ]
        )
        return Provision(
            command=f"uv run {' '.join(flags)} {command}",
            note=(
                f"uv run{' (uv.lock)' if locked else ''} — {synced} synced, "
                f"{', '.join(injected)} injected"
            ),
        )

    if requirements:
        declared = _names(lines)
        injected = [
            "pytest-cov",
            "pip",
            *(p for p in plugins if _canonical(p) not in declared),
        ]
        # A `src/` package is importable only once installed, and the requirements
        # file is not what installs it: `--with-editable .` is. A flat layout imports
        # from the project directory, which `python -m` puts on sys.path.
        editable = ["--with-editable ."] if has_pep621 and (root / "src").is_dir() else []
        flags = [
            "--no-project",
            *(f"--with {package}" for package in injected),
            *editable,
            f"--with-requirements {requirements}",
        ]
        run = f"python -m {command}" if command.startswith("pytest") else command
        why = " (every requirement pinned: it is the lock)" if has_pep621 else ""
        return Provision(
            command=f"uv run {' '.join(flags)} {run}",
            note=f"uv run --with-requirements {requirements}{why}, {', '.join(injected)} injected",
        )

    return Provision(note="no installable Python project found — running tests as-is")


#: The coverage providers vitest can load. `--coverage` needs one installed next to vitest,
#: and without it vitest prints `MISSING DEPENDENCY Cannot find dependency
#: '@vitest/coverage-v8'`, writes no report, and the run is broken.
_VITEST_PROVIDERS = ("@vitest/coverage-v8", "@vitest/coverage-istanbul")

#: Installs vitest's default provider AT vitest's own installed version: a provider from
#: another release refuses to load. `--no-save` leaves package.json and the lockfile as
#: they were. `latest` only when vitest is not in node_modules, where `npx` fetches the
#: latest vitest too, so the two still agree.
_VITEST_PROVIDER_INSTALL = (
    "npm install --no-save --no-audit --no-fund "
    '"@vitest/coverage-v8@$(node -p '
    '"require(\'./node_modules/vitest/package.json\').version" 2>/dev/null || echo latest)"'
)


def _declares(root: Path, packages: tuple[str, ...]) -> bool:
    """True if package.json declares any of ``packages``, or node_modules already has one."""
    try:
        data = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    declared: set[str] = set()
    if isinstance(data, dict):
        for table in ("dependencies", "devDependencies"):
            if isinstance(data.get(table), dict):
                declared.update(data[table])
    return any(p in declared or (root / "node_modules" / p).is_dir() for p in packages)


def _javascript_plan(root: Path, which: Which, eco: Ecosystem) -> Provision:
    """Install the repo's node modules when nothing else has.

    The same bug as Python's, one letter at a time: ``npx --yes jest`` cheerfully
    downloads jest into a cache and then fails to import a single one of the repo's
    own modules, which surfaces as a failed run rather than as "nothing installed the
    dependencies". Only when ``node_modules`` is genuinely absent, so a job that did
    its own install pays nothing.

    ``npm ci`` needs a lockfile and refuses when it is out of step with
    ``package.json``; ``npm install`` handles both cases and is the honest fallback.
    The ``||`` is why this is one command and not two — the second only runs when the
    first fails, which a list of must-all-succeed steps cannot express.

    A vitest suite also needs a coverage provider, which is Brimyr's requirement in
    the way pytest-cov is: a repo can run `vitest run` green for ever without one, and
    MagmaMoose/dunmir's frontend does. Installed only when the repo declares neither.
    """
    setup: list[str] = []
    notes: list[str] = []
    if (root / "node_modules").is_dir():
        notes.append("node_modules already present")
    elif not which("npm"):
        return Provision(note="`npm` is not on PATH — running tests as-is")
    else:
        setup.append("npm ci --no-audit --no-fund || npm install --no-audit --no-fund")
        notes.append("npm ci (node_modules was absent)")
    if "vitest" in eco.test_command and not _declares(root, _VITEST_PROVIDERS):
        if which("npm"):
            setup.append(_VITEST_PROVIDER_INSTALL)
            notes.append("@vitest/coverage-v8 injected at vitest's version")
        else:
            notes.append("no vitest coverage provider and no `npm` to install one")
    return Provision(setup=tuple(setup), note="; ".join(notes))


def _go_plan(which: Which) -> Provision:
    """Go fetches its own modules during `go test`; the one gap is `go` itself.

    Nothing is installed here: `action.yml` provisions Go when the runner has none,
    and a missing binary is then the shell's `127`, reported as "nothing ran" with
    this note beside it.
    """
    if not which("go"):
        return Provision(note="`go` is not on PATH — running tests as-is")
    return Provision(note="Go restores its own modules")


#: Where the kcov wrap writes, matching the `shell` ecosystem's `coverage_paths`. kcov
#: leaves one Cobertura per traced binary plus a merged one; both are ingested and
#: merging is covered-wins, so the overlap is idempotent.
_KCOV_OUT = "coverage/kcov"

#: kcov instruments every script it sees execute, including the test files themselves.
#: Counting `.bats` files as covered source inflates the number with the tests' own
#: lines — the denominator is meant to be the scripts under test.
_KCOV_EXCLUDE = "/.git/,/node_modules/,.bats"


def _shell_plan(command: str, which: Which) -> Provision:
    """Make a bats suite runnable, and measurable only if kcov is already there.

    Two independent gaps, and only one of them is Brimyr's to close.

    **bats** is closed the way JavaScript's is: `npx --yes bats` fetches bats-core into
    a cache. Without it a runner with no bats gets a shell `127`, which is a broken run
    and a red gate on a repo whose tests are fine — the exact failure `provision` exists
    to prevent, and the reason detecting shell is safe to do fleet-wide at all.

    **kcov** is deliberately NOT closed. There is no repo-owned manager to install it
    through: it is a system package (`apt-get install kcov`) or a source build, so
    provisioning it would mean either sudo-ing into the caller's runner image for every
    shell repo on the estate or paying minutes of build time per job [cost]. So it is
    used when present and never installed — and because its absence is the normal case,
    `shell` is `coverage_optional`: a bats suite that passes and measures nothing is a
    PASS that says it measured nothing, not a broken run.
    """
    runner_note = ""
    if not which("bats"):
        if not which("npx"):
            return Provision(note="neither `bats` nor `npx` is on PATH — running tests as-is")
        # bats-core publishes itself to npm under `bats`; `npx --yes` is the same
        # cache-and-run path the JS ecosystem already relies on.
        command = f"npx --yes {command}"
        runner_note = "npx --yes bats (bats was not on PATH)"

    if not which("kcov"):
        return Provision(
            command=command,
            note=(f"{runner_note}; " if runner_note else "")
            + "no `kcov` on PATH — bats will run without coverage instrumentation",
        )

    wrapped = f"kcov --include-path=. --exclude-pattern={_KCOV_EXCLUDE} {_KCOV_OUT} {command}"
    return Provision(
        command=wrapped,
        note=(f"{runner_note}; " if runner_note else "") + f"kcov wrap -> {_KCOV_OUT}",
    )


def plan(
    eco: Ecosystem,
    repo: str | Path = ".",
    *,
    command: str | None = None,
    which: Which = shutil.which,
) -> Provision:
    """Work out how to make ``eco``'s tests runnable in ``repo``.

    ``command`` is the test command being wrapped; it defaults to the ecosystem's
    detected one. Maven and .NET restore their own dependencies as part of the test
    run, so they always plan to nothing — there is no gap there to close.
    """
    root = Path(repo)
    cmd = command or eco.command_str()
    if eco.key == "python":
        return _python_plan(root, cmd, which)
    if eco.key == "javascript":
        return _javascript_plan(root, which, eco)
    if eco.key == "go":
        return _go_plan(which)
    if eco.key == "shell":
        return _shell_plan(cmd, which)
    return Provision(note=f"{eco.label} restores its own dependencies")
