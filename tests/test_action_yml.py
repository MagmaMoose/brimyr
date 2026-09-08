"""Guards on action.yml's wiring for running a consumer's tests.

Brimyr runs the repo's OWN test suite on the runner, so the toolchain has to be
there. Nothing in the action installs a consumer's dependencies, and when the
command is missing the broken-run rule reports a tool error — correct, but it reads
as a brimyr bug rather than a missing install. These pin the two things that make
the Python path work at all.
"""

from __future__ import annotations

import re
from pathlib import Path

ACTION_YML = Path(__file__).parent.parent / "action.yml"


def _action() -> str:
    return ACTION_YML.read_text(encoding="utf-8")


def test_uv_is_installed_for_uv_projects():
    # detect.py resolves a uv project's command to `uv run pytest ...`; without this
    # step that trades "pytest: not found" for "uv: not found".
    action = _action()
    assert "astral-sh/setup-uv@" in action
    assert re.search(r"astral-sh/setup-uv@[0-9a-f]{40} # v", action), "must be SHA-pinned"


def test_the_uv_step_keys_on_the_same_signal_as_detection():
    # detect._PYTHON_ENV_MANAGERS keys on uv.lock. If the action gated on anything
    # else the two could disagree about whether this is a uv repo, and the mismatch
    # only ever shows up as a broken run in a consumer's CI.
    from brimyr.detect import _PYTHON_ENV_MANAGERS

    assert [lock for lock, _ in _PYTHON_ENV_MANAGERS] == ["uv.lock"]
    assert "if: ${{ hashFiles('uv.lock') != '' }}" in _action()


def test_the_action_exposes_setup_like_the_reusable_workflow():
    # gate.yml has carried `setup` since the start; the action did not, which left
    # every consumer of the ACTION (the org template's shape) with no way to install
    # a toolchain at all.
    action = _action()
    assert re.search(r"^  setup:", action, re.MULTILINE)
    assert "- name: Setup" in action


def test_setup_reaches_the_shell_through_the_environment():
    # An input spliced straight into `run:` can terminate the surrounding shell
    # syntax. gate.yml's own Setup step predates this; the action's must not repeat it.
    action = _action()
    assert "BRIMYR_SETUP: ${{ inputs.setup }}" in action
    assert 'run: bash -c "$BRIMYR_SETUP"' in action


def test_both_surfaces_offer_setup():
    """gate.yml and action.yml must not diverge on how deps get installed.

    Grep-based on purpose: brimyr's dev group has no YAML parser (the core is
    stdlib-only), and one test is not worth a dependency.
    """
    gate = (ACTION_YML.parent / ".github/workflows/gate.yml").read_text(encoding="utf-8")
    assert re.search(r"^      setup:", gate, re.MULTILINE)
    assert "- name: Setup" in gate
    assert re.search(r"^  setup:", _action(), re.MULTILINE)
