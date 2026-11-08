"""Where a gate runs inside a worktree.

The rig learned this the hard way: `specify_task` ran its red gate in `<worktree>/backend`, a directory
that exists in the OCCC monorepo and does not exist here, so a perfectly red suite was reported as a
failed gate. `app_root` asks the worktree instead of assuming the contour.
"""

from __future__ import annotations

from pathlib import Path

from rig.rig_conf import app_root


def test_a_monorepo_worktree_runs_the_gates_in_backend(tmp_path: Path):
    (tmp_path / "backend").mkdir()

    assert app_root(tmp_path) == tmp_path / "backend"


def test_a_root_layout_worktree_runs_the_gates_at_the_root(tmp_path: Path):
    """This repository keeps `bin/`, `tools/` and `config/` at the top: no contour to enter."""
    (tmp_path / "bin").mkdir()
    (tmp_path / "tools").mkdir()

    assert app_root(tmp_path) == tmp_path
