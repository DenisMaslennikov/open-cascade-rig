"""The specifier's file gate: a stub where the plan says, a unit test in the feature's own folder.

Two rules meet here. A stub must land in a source root the plan alphabet admits (`tools/`, `bin/rig/`,
`alembic/` — card #205); a unit test must land in the feature's own `tests/` directory and must be one
of the files the plan froze. Nothing pairs a test with a source file by name: coverage answers what is
covered, the file system is not asked.
"""

from __future__ import annotations

from pathlib import Path

from rig.specify_task import write_files

STUB = "def store_seen() -> None:\n    raise NotImplementedError\n"
TEST = "def test_store_seen():\n    assert True\n"


def write(worktree: Path, files: list[dict], allowed: list[str], plan_tests: set[str] | None = None):
    return write_files(worktree, files, allowed, force=False, plan_tests=plan_tests)


def test_a_stub_under_tools_is_accepted(tmp_path: Path):
    """`tools/` is a source root of this repository; the gate asks the alphabet, not `src/`."""
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


def test_a_unit_test_in_the_feature_folder_is_accepted(tmp_path: Path):
    """`tools/wrapper/rail_db.py` -> `tools/wrapper/tests/test_rail_db.py`: the suite travels with it."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/tests/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
    )

    assert ok, errors


def test_a_frozen_test_named_by_the_plan_is_accepted(tmp_path: Path):
    """The plan names the file, the gate accepts it — the plan is the decision, not a naming rule."""
    ok, errors = write(
        tmp_path,
        [{"path": "bin/tests/test_wrapper_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"bin/tests/test_wrapper_rail_db.py"},
    )

    assert ok, errors


def test_without_a_frozen_list_any_tests_path_is_the_plans_choice(tmp_path: Path):
    """The plan owns the test paths; the gate does not compare them with source paths."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/tests/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
    )

    assert ok, errors


def test_a_test_file_the_plan_did_not_freeze_is_rejected(tmp_path: Path):
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/tests/test_other.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"bin/tests/test_wrapper_rail_db.py"},
    )

    assert not ok
    assert any("the plan froze no such test file" in error for error in errors)


def test_a_test_beside_its_module_is_rejected(tmp_path: Path):
    """A test next to the code it tests is not a `tests/` directory."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
    )

    assert not ok
    assert any("unit test lives at" in error for error in errors)
