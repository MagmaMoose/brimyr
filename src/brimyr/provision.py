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

import shutil
from collections.abc import Callable
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


def _python_plan(root: Path, command: str, which: Which) -> Provision:
    """How this Python repo installs itself, and how to run pytest inside that.

    ``uv`` first, and for one reason: it is the only tool here that installs the
    project, its PEP 735 dev dependency group *and* a test-only package that the repo
    does not declare, in a single command, from ``uv.lock`` when there is one. That
    last part matters more than it looks — ``pytest-cov`` is a Brimyr requirement, not
    the repo's, so a repo can have a perfectly good pytest setup and still have no way
    to satisfy ``--cov``. ``--with pytest-cov`` supplies it without touching the
    repo's own declared dependencies, and because ``pytest-cov`` depends on ``pytest``
    it also covers a repo that has test files but never declared the runner.

    Poetry gets its own branch only for the pre-2.0 layout, where dependencies live
    under ``[tool.poetry.dependencies]`` and there is no ``[project]`` table at all —
    uv reads that repo as having no dependencies and would install none of them.
    Poetry 2.x writes a standard ``[project]`` table and goes down the uv path like
    everything else.
    """
    pyproject = _read(root / "pyproject.toml")
    has_pep621 = "[project]" in pyproject
    poetry_only = "[tool.poetry" in pyproject and not has_pep621

    if poetry_only:
        if not which("poetry"):
            return Provision(
                note="poetry project, but `poetry` is not on PATH — running tests as-is"
            )
        setup = ["poetry install --no-interaction --no-ansi"]
        if "pytest-cov" not in pyproject:
            # Into poetry's OWN virtualenv, never the ambient interpreter. Skipped when
            # the repo already declares the plugin, so a project that pins a version
            # keeps it.
            setup.append(
                "poetry run python -m pip install --disable-pip-version-check --quiet pytest-cov"
            )
        return Provision(
            setup=tuple(setup),
            command=f"poetry run {command}",
            note="poetry install + poetry run",
        )

    if not which("uv"):
        return Provision(note="`uv` is not on PATH — running tests as-is")

    if (root / "uv.lock").is_file() or has_pep621 or "[tool.uv]" in pyproject:
        locked = " (uv.lock)" if (root / "uv.lock").is_file() else ""
        return Provision(
            command=f"uv run --with pytest-cov {command}",
            note=f"uv run{locked} — project and dev group synced, pytest-cov injected",
        )

    requirements = _first_existing(root, _REQUIREMENTS)
    if requirements:
        # No project to install, so the suite runs against an ephemeral environment
        # built from the requirements file. Imports of the repo's own modules rely on
        # the layout or conftest, exactly as they do when a developer runs pytest from
        # the repo root with the requirements installed.
        return Provision(
            command=f"uv run --with pytest-cov --with-requirements {requirements} {command}",
            note=f"uv run --with-requirements {requirements}, pytest-cov injected",
        )

    return Provision(note="no installable Python project found — running tests as-is")


def _javascript_plan(root: Path, which: Which) -> Provision:
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
    """
    if (root / "node_modules").is_dir():
        return Provision(note="node_modules already present")
    if not which("npm"):
        return Provision(note="`npm` is not on PATH — running tests as-is")
    return Provision(
        setup=("npm ci --no-audit --no-fund || npm install --no-audit --no-fund",),
        note="npm ci (node_modules was absent)",
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
        return _javascript_plan(root, which)
    return Provision(note=f"{eco.label} restores its own dependencies")
