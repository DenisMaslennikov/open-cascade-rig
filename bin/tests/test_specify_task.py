"""The specifier's file gate: where a stub and a test may live in *this* repository.

Card #203's first stub pass died with `a stub must live under src/`: the gate restated the src-layout
convention instead of reading the plan alphabet, so a repository whose sources are `tools/`, `bin/rig/`
and `alembic/` could not produce a single stub. These tests pin the gate against the alphabet and keep
the src-layout mirror working for the projects that do use it.
"""

from __future__ import annotations

from pathlib import Path

from rig.specify_task import write_files

STUB = "def store_seen() -> None:\n    raise NotImplementedError\n"
TEST = "def test_store_seen():\n    assert True\n"


def write(worktree: Path, files: list[dict], allowed: list[str], plan_tests: set[str] | None = None):
    return write_files(worktree, files, allowed, force=False, plan_tests=plan_tests)


def test_a_stub_under_tools_is_accepted(tmp_path: Path):
    """`tools/` is a source root of this repository; the gate must ask the alphabet, not `src/`."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/rail_db.py", "kind": "stub", "content": STUB}],
        ["tools/wrapper/rail_db.py"],
    )

    assert ok, errors


def test_a_stub_in_the_rigs_own_package_is_accepted(tmp_path: Path):
    ok, errors = write(
        tmp_path,
        [{"path": "bin/rig/rail_db.py", "kind": "stub", "content": STUB}],
        ["bin/rig/rail_db.py"],
    )

    assert ok, errors


def test_a_stub_outside_every_source_root_is_rejected(tmp_path: Path):
    ok, errors = write(
        tmp_path,
        [{"path": "config/schema/rail_db.py", "kind": "stub", "content": STUB}],
        ["config/schema/rail_db.py"],
    )

    assert not ok
    assert any("stub must live under" in error for error in errors)


def test_a_frozen_test_named_by_the_plan_is_accepted(tmp_path: Path):
    """`bin/tests/` is this repository's test root; the plan names the file, the gate accepts it."""
    ok, errors = write(
        tmp_path,
        [{"path": "bin/tests/test_wrapper_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"bin/tests/test_wrapper_rail_db.py"},
    )

    assert ok, errors


def test_a_mirrored_src_test_path_is_not_invented_for_a_tools_stub(tmp_path: Path):
    """The `tests/unit/...` mirror belongs to src layouts; for `tools/` it is a wrong path."""
    ok, errors = write(
        tmp_path,
        [{"path": "tests/unit/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"bin/tests/test_wrapper_rail_db.py"},
    )

    assert not ok
    assert any("the plan froze no such test file" in error for error in errors)


def test_a_test_beside_its_module_is_rejected(tmp_path: Path):
    """A test next to the code it tests is not a test root of this repository."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
    )

    assert not ok
    assert any("a test must live at one of" in error for error in errors)


def test_a_plan_named_test_outside_a_test_root_is_rejected(tmp_path: Path):
    """Even a path the plan names must land under a test directory, not under `tools/`."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"tools/wrapper/test_rail_db.py"},
    )

    assert not ok
    assert any("test file must live under" in error for error in errors)
