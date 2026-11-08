"""Shared setup for the suite: find the repository by a marker, not by how deep this file sits.

The suite also runs from mutmut's copy, `bin/mutants/tests/`, which is one directory deeper than
`bin/tests/`, so counting parents would land outside the repository and the wrapper would vanish
from `sys.path`. `AGENTS.md` and `.git` sit at the repository root in both places.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest


def _repository_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "AGENTS.md").is_file() and (candidate / ".git").exists():
            return candidate
    raise RuntimeError("repository root not found: no parent directory holds AGENTS.md and .git")


REPO_ROOT = _repository_root()

# Two import forms, both live: `bin/tests/test_wrapper_vikunja_client.py` imports `vikunja_client`
# flat, and the rail modules import each other as `tools.wrapper.<module>` (a namespace package, so
# the repository root must be importable). The suite runs from `bin`, where neither is on the path.
sys.path.insert(0, str(REPO_ROOT / "tools" / "wrapper"))
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT
