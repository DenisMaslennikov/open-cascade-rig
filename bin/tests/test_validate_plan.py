"""The plan validator's own contract: where the schema lives, and what a plan may name.

Card #191 moved the modules from `bin/` to `bin/rig/`, and the schema path built as `<here>/../schema`
started pointing at a directory that does not exist — the validator crashed on every plan, which the
suite never noticed because nothing drove it. These tests pin the resolution and the alphabet.

The validator no longer guesses what a path is: a subtask declares `kind` (`code`, `tests`, `docs`) and
the plan is read as written. A path prefix tells the validator nothing about the file's role, so it is
not asked.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from rig.validate_plan import check_plan, main, schema_path

SRC_PREFIXES = ["src", "app", "lib", "pkg", "bin", "tools", "alembic"]


def plan(*subtasks: tuple[str, list[str]] | tuple[str, list[str], str]) -> dict:
    """A minimal plan: (id, files_allowed) or (id, files_allowed, kind) triples, no dependencies."""
    return {
        "task": {
            "id": "probe-task",
            "title": "Probe",
            "goal": "Drive the validator from a test instead of from the command line.",
            "acceptance_summary": "The validator answers with a list of errors.",
        },
        "subtasks": [
            {
                "id": entry[0],
                "title": f"Subtask {entry[0]}",
                "goal": "A subtask with the files this test names.",
                "kind": entry[2] if len(entry) == 3 else "code",
                "files_allowed": entry[1],
                "acceptance": [{"in": "input", "out": "output"}],
                "depends_on": [],
            }
            for entry in subtasks
        ],
    }


def test_schema_is_found_from_the_package_not_by_counting_parents():
    """The schema lives in `config/schema/` at the checkout's root, wherever the module sits."""
    path = schema_path()

    assert path.is_file()
    assert path.parent.name == "schema"
    assert path.parent.parent.name == "config"
    assert "subtasks" in json.loads(path.read_text(encoding="utf-8"))["properties"]


def test_a_root_alembic_tree_and_dependency_pins_are_plannable():
    """`alembic.ini`, `alembic/…`, `requirements-dev.txt` and `pyproject.toml` are legal targets.

    A card that introduces migrations or pins a dependency owns files at the repository root; before
    the alphabet admitted them, the architect had to answer such a card with `needs_clarification`.
    """
    errors = check_plan(
        plan(
            ("migration", ["alembic/env.py", "alembic/versions/0001_seen.py", "alembic.ini"]),
            ("pins", ["requirements-dev.txt", "pyproject.toml"]),
            ("model", ["tools/wrapper/rail_db.py"]),
        )
    )

    assert errors == []


def test_a_tests_subtask_declares_itself_instead_of_being_detected_by_its_path():
    """The plan says `kind: tests`; the validator does not read directories to find out."""
    errors = check_plan(
        plan(
            ("rail-tests", ["bin/tests/test_wrapper_rail_state.py"], "tests"),
            ("rail-code", ["tools/wrapper/rail_state.py"], "code"),
        )
    )

    assert errors == []


def test_a_subtask_without_a_kind_is_rejected():
    """`kind` is required: an unlabelled subtask is a plan the executor cannot be gated against."""
    broken = plan(("one", ["tools/wrapper/rail_db.py"]))
    del broken["subtasks"][0]["kind"]

    errors = check_plan(broken)

    assert any("kind" in error for error in errors)


def test_an_unknown_kind_is_rejected():
    errors = check_plan(plan(("one", ["tools/wrapper/rail_db.py"], "sources")))

    assert any("kind" in error for error in errors)


def test_a_plan_without_a_code_subtask_is_rejected():
    """Documentation and tests alone are not a covered task."""
    errors = check_plan(plan(("docs", ["docs/hermes-session-store.md"], "docs")))

    assert any("kind: code" in error for error in errors)


@given(prefix=st.sampled_from(SRC_PREFIXES), name=st.from_regex(r"[a-z][a-z0-9_]{2,20}\.py", fullmatch=True))
def test_any_path_under_an_allowed_prefix_validates(prefix: str, name: str):
    """For every source root the repository actually uses, one path in it is a legal plan entry."""
    errors = check_plan(plan(("one", [f"{prefix}/{name}"])))

    assert errors == []


@pytest.mark.parametrize(
    "path",
    [
        "config/schema/plan.schema.json",  # the rig's config is not a task's target
        "README.md",  # a root file outside the pinned list
        "docs/../bin/rig/gate.py",  # a traversal dressed as an allowed prefix
        "/etc/passwd",
    ],
)
def test_paths_outside_the_alphabet_are_rejected(path: str):
    """The alphabet is a lock, so the paths it refuses stay refused."""
    errors = check_plan(plan(("one", [path])))

    assert errors, f"{path} should not validate"


def test_main_parses_the_plan_and_the_repo_with_argparse(tmp_path: Path):
    """The CLI is argparse, not hand-rolled argv: one positional plan and an optional --repo."""
    probe = tmp_path / "plan.json"
    probe.write_text(
        json.dumps(plan(("one", ["tools/wrapper/rail_db.py"]))),
        encoding="utf-8",
    )

    assert main([str(probe), "--repo", str(tmp_path)]) == 0


def test_main_reports_an_invalid_plan_with_exit_code_one(tmp_path: Path):
    broken = plan(("one", ["tools/wrapper/rail_db.py"]))
    broken["subtasks"][0]["files_allowed"] = ["config/schema/plan.schema.json"]
    probe = tmp_path / "plan.json"
    probe.write_text(json.dumps(broken), encoding="utf-8")

    assert main([str(probe)]) == 1


def test_main_without_a_plan_exits_two():
    """argparse owns the usage message; a missing argument is a usage error, not a crash."""
    with pytest.raises(SystemExit) as exit_info:
        main([])

    assert exit_info.value.code == 2
