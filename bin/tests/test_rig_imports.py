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

# Four modules import the board adapter, which lives in `~/soft/vikunja/board.py` — a library that is
# not in this repository and therefore not on CI. They import it at module level on purpose (the code
# style rule forbids function-local imports), so on a runner without that file they cannot import at
# all. The skip is narrow and named: a module added to this set without the library present is a
# dependency decision, not a test detail.
BOARD_DEPENDENT = {"board_archive", "board_links", "board_seed", "board_sync"}
BOARD_LIB = Path.home() / "soft" / "vikunja" / "board.py"


def test_the_package_has_modules_to_check():
    """A vacuous parametrisation would hide the regression instead of catching it."""
    assert len(MODULES) > 10
    assert {"specify_task", "pr_review", "run_subtask", "validate_plan"} <= set(MODULES)


def test_every_board_dependent_module_is_still_in_the_package():
    """The skip list may not outlive its modules or grow silently."""
    assert BOARD_DEPENDENT <= set(MODULES)


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module: str):
    """`import rig.<module>` succeeds: no function-local sibling import, no missing package member."""
    if module in BOARD_DEPENDENT and not BOARD_LIB.is_file():
        pytest.skip(f"{module} needs ~/soft/vikunja/board.py, which CI does not have")

    assert importlib.import_module(f"rig.{module}") is not None
