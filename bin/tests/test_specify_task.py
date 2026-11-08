"""The specifier's file gate: the plan decides what a file is.

The architect names the files of a subtask; the specifier writes stubs into those source files and tests
into the test files the plan froze. Nothing here reads a path to guess whether it is a test: a path is a
test only because the plan named it as one.
"""

from __future__ import annotations

from pathlib import Path

from rig.specify_task import write_files

STUB = "def store_seen() -> None:\n    raise NotImplementedError\n"
TEST = "def test_store_seen():\n    assert True\n"


def write(worktree: Path, files: list[dict], allowed: list[str], plan_tests: set[str] | None = None):
    return write_files(worktree, files, allowed, force=False, plan_tests=plan_tests)


def test_a_stub_in_the_planned_file_is_accepted(tmp_path: Path):
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


def test_a_file_outside_files_allowed_is_rejected(tmp_path: Path):
    """The scope lock is `files_allowed`, not a directory convention."""
    ok, errors = write(
        tmp_path,
        [{"path": "config/schema/rail_db.py", "kind": "stub", "content": STUB}],
        ["tools/wrapper/rail_db.py"],
    )

    assert not ok
    assert any("outside files_allowed" in error for error in errors)


def test_a_test_file_the_plan_froze_is_accepted(tmp_path: Path):
    ok, errors = write(
        tmp_path,
        [{"path": "bin/tests/test_wrapper_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"bin/tests/test_wrapper_rail_db.py"},
    )

    assert ok, errors


def test_a_test_file_the_plan_did_not_name_is_rejected(tmp_path: Path):
    """The specifier may not invent a test path, whatever directory it points at."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/tests/test_other.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
        plan_tests={"bin/tests/test_wrapper_rail_db.py"},
    )

    assert not ok
    assert any("the plan names no such test file" in error for error in errors)


def test_a_test_without_a_planned_path_is_rejected(tmp_path: Path):
    """No plan-named test file means no test file: the specifier does not choose its own home."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/tests/test_rail_db.py", "kind": "test", "content": TEST}],
        ["tools/wrapper/rail_db.py"],
    )

    assert not ok
    assert any("the plan names no such test file" in error for error in errors)


def test_a_stub_that_does_not_raise_is_rejected(tmp_path: Path):
    """A stub must be red for the right reason: `NotImplementedError`, not a working body."""
    ok, errors = write(
        tmp_path,
        [{"path": "tools/wrapper/rail_db.py", "kind": "stub", "content": "VALUE = 1\n"}],
        ["tools/wrapper/rail_db.py"],
    )

    assert not ok
    assert any("NotImplementedError" in error for error in errors)
