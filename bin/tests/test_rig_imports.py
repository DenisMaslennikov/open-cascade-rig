"""Every rig module must import as a package member.

Card #191 moved the modules from `bin/` to `bin/rig/`. Two of them — `specify_task` and `pr_review` —
kept a function-local `from plan_task import …`, which only resolved while the modules were flat in a
directory that `bin` put on `sys.path`. After the move both crashed the moment the pipeline reached
them, and no test noticed: the whole runner sat outside the suite's reach.

An import is the cheapest possible contract, and it is the one the move broke, so it is the one pinned
here: import every module in `bin/rig/`, one test case each, so the failure names the file.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

RIG_DIR = Path(__file__).resolve().parents[1] / "rig"
MODULES = sorted(
    path.stem for path in RIG_DIR.glob("*.py") if path.stem != "__init__" and not path.stem.startswith("_")
)


def test_the_package_has_modules_to_check():
    """A vacuous parametrisation would hide the regression instead of catching it."""
    assert len(MODULES) > 10
    assert {"specify_task", "pr_review", "run_subtask", "validate_plan"} <= set(MODULES)


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str):
    """`import rig.<module>` succeeds: no function-local sibling import, no missing package member."""
    assert importlib.import_module(f"rig.{module}") is not None
