"""A project directory reached twice through the filesystem is one project, not two.

`usage.py` builds its project list from `STATE.iterdir()`, which follows symlinks. On the owner's
machine `open-capsuleer-command-center` is a symlink to `occc`, so `report` reads the same ledger a
second time and the grand total doubles that project's spend, while `projects` lists the alias as a
project of its own. These tests freeze the rule the fix must restore — one directory, one ledger, one
count — and keep the contract of an explicit `--project` and of a state root with no data.

The CLI is driven through `main()` with `sys.argv` patched, as an operator runs it, because the module
builds its parser inside `main()` and resolves `STATE` at call time.
"""

from __future__ import annotations

import json
import re
import string
import sys
import tempfile
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rig import usage

PROJECT = "occc"
ALIAS = "open-capsuleer-command-center"

MODEL_A = "vendor/model-a"
MODEL_B = "vendor/model-b"


def _event(
    role: str,
    model: str,
    feature: str,
    task: str,
    in_tokens: int,
    out_tokens: int,
    cost: float,
    project: str = PROJECT,
) -> dict:
    """One ledger event in the shape `cmd_record` writes."""
    return {
        "ts": "2026-11-08T10:00:00+00:00",
        "project": project,
        "feature": feature,
        "task": task,
        "role": role,
        "model": model,
        "input_tokens": in_tokens,
        "output_tokens": out_tokens,
        "cache_read_tokens": 0,
        "cost": cost,
        "note": "",
    }


EVENTS = [
    _event("executor", MODEL_A, "industry", "t2-materials", 1000, 500, 0.03),
    _event("reviewer", MODEL_B, "review", "t3-scoring", 2000, 100, 0.02),
]

EXPECTED_CALLS = 2
EXPECTED_IN = 3000
EXPECTED_OUT = 600
EXPECTED_COST = 0.05

# The grand-total line `fmt` builds: call count, input tokens, output tokens, cost.
_GRAND_TOTAL = re.compile(r"(\d+)\s+вызовов\s+in\s+(\d+)\s+out\s+(\d+)\s+=\s+(-?\d+\.\d+)")


def _write_ledger(state: Path, project: str, events: list[dict]) -> None:
    path = state / project / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _state(monkeypatch, root: Path, alias: str = ALIAS, events: list[dict] | None = None) -> Path:
    """Build a state root whose project ledger is also reachable through a real directory symlink."""
    state = root / "state"
    monkeypatch.setattr(usage, "STATE", state)
    _write_ledger(state, PROJECT, EVENTS if events is None else events)
    (state / alias).symlink_to(state / PROJECT, target_is_directory=True)
    return state


def _run(monkeypatch, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["usage", *argv])
    return usage.main()


def _grand(stdout: str) -> tuple[list[str], dict]:
    """Return the projects named in the grand-total line and the numbers it carries."""
    for line in stdout.splitlines():
        if not line.startswith("ИТОГО по "):
            continue
        found = _GRAND_TOTAL.search(line)
        if found is None:
            raise AssertionError(f"the grand-total line carries no numbers: {line!r}")
        names = [name.strip() for name in line.removeprefix("ИТОГО по ").split(":", 1)[0].split(",")]
        return names, {
            "calls": int(found.group(1)),
            "in": int(found.group(2)),
            "out": int(found.group(3)),
            "cost": float(found.group(4)),
        }
    raise AssertionError(f"the report has no grand-total line:\n{stdout}")


def _blocks(stdout: str) -> list[str]:
    """The projects that have their own section in the report."""
    return [line.removeprefix("■ ").split(":", 1)[0] for line in stdout.splitlines() if line.startswith("■ ")]


def test_a_symlinked_project_is_reported_once_and_counted_once(monkeypatch, tmp_path, capsys):
    _state(monkeypatch, tmp_path)

    assert _run(monkeypatch, "report") == 0
    stdout = capsys.readouterr().out

    names, grand = _grand(stdout)
    assert names == [PROJECT]
    assert _blocks(stdout) == [PROJECT]
    assert ALIAS not in stdout
    assert grand["calls"] == EXPECTED_CALLS
    assert grand["in"] == EXPECTED_IN
    assert grand["out"] == EXPECTED_OUT
    assert grand["cost"] == pytest.approx(EXPECTED_COST)


def test_the_json_grand_total_counts_the_symlinked_ledger_once(monkeypatch, tmp_path, capsys):
    _state(monkeypatch, tmp_path)

    assert _run(monkeypatch, "report", "--json") == 0
    payload = json.loads(capsys.readouterr().out)

    assert list(payload["projects"]) == [PROJECT]
    assert payload["grand_total"]["calls"] == EXPECTED_CALLS
    assert payload["grand_total"]["in"] == EXPECTED_IN
    assert payload["grand_total"]["out"] == EXPECTED_OUT
    assert payload["grand_total"]["cost"] == pytest.approx(EXPECTED_COST)


@pytest.mark.parametrize("alias", ["aaa-occc", "zzz-occc"])
def test_the_real_directory_is_the_name_that_survives_whichever_way_the_alias_sorts(
    monkeypatch, tmp_path, capsys, alias
):
    _state(monkeypatch, tmp_path, alias=alias)

    assert _run(monkeypatch, "report") == 0
    stdout = capsys.readouterr().out

    names, _ = _grand(stdout)
    assert names == [PROJECT]
    assert _blocks(stdout) == [PROJECT]
    assert alias not in stdout


def test_an_explicit_project_keeps_its_numbers(monkeypatch, tmp_path, capsys):
    state = _state(monkeypatch, tmp_path)
    _write_ledger(state, "other", [_event("archivist", MODEL_A, "docs", "t9-notes", 7000, 7000, 1.0, project="other")])

    assert _run(monkeypatch, "report", "--project", PROJECT) == 0
    stdout = capsys.readouterr().out

    names, grand = _grand(stdout)
    assert names == [PROJECT]
    assert _blocks(stdout) == [PROJECT]
    assert grand["calls"] == EXPECTED_CALLS
    assert grand["in"] == EXPECTED_IN
    assert grand["out"] == EXPECTED_OUT
    assert grand["cost"] == pytest.approx(EXPECTED_COST)


def test_the_project_listing_shows_each_ledger_once(monkeypatch, tmp_path, capsys):
    _state(monkeypatch, tmp_path)

    assert _run(monkeypatch, "projects") == 0
    stdout = capsys.readouterr().out

    lines = [line for line in stdout.splitlines() if line.strip()]
    assert len(lines) == 1
    assert lines[0].split()[0] == PROJECT
    assert f"событий: {len(EVENTS)}" in lines[0]
    assert ALIAS not in stdout


def test_report_says_no_data_and_fails_when_the_state_root_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(usage, "STATE", tmp_path / "state")

    assert _run(monkeypatch, "report") == 1
    captured = capsys.readouterr()

    assert "нет данных" in captured.err
    assert "ИТОГО" not in captured.out


def test_projects_says_no_data_and_succeeds_when_the_state_root_is_missing(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(usage, "STATE", tmp_path / "state")

    assert _run(monkeypatch, "projects") == 0

    assert "нет данных" in capsys.readouterr().out


_ALIAS_PARTS = st.lists(
    st.text(alphabet=string.ascii_lowercase, min_size=1, max_size=6),
    min_size=1,
    max_size=4,
    unique=True,
)
_AMOUNT = st.integers(min_value=0, max_value=100_000)
_COST = st.integers(min_value=0, max_value=10_000).map(lambda cents: cents / 100)
_GENERATED_EVENTS = st.lists(
    st.builds(
        _event,
        role=st.just("executor"),
        model=st.sampled_from([MODEL_A, MODEL_B]),
        feature=st.sampled_from(["industry", "review"]),
        task=st.sampled_from(["t2-materials", "t3-scoring"]),
        in_tokens=_AMOUNT,
        out_tokens=_AMOUNT,
        cost=_COST,
    ),
    min_size=1,
    max_size=4,
)


@pytest.mark.property
@settings(
    max_examples=25,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(parts=_ALIAS_PARTS, events=_GENERATED_EVENTS)
def test_no_number_of_aliases_adds_anything_to_the_grand_total(monkeypatch, capsys, parts, events):
    with tempfile.TemporaryDirectory() as root:
        state = Path(root) / "state"
        monkeypatch.setattr(usage, "STATE", state)
        _write_ledger(state, PROJECT, events)
        for part in parts:
            (state / f"{part}-occc").symlink_to(state / PROJECT, target_is_directory=True)

        assert _run(monkeypatch, "report", "--json") == 0
        payload = json.loads(capsys.readouterr().out)

    assert list(payload["projects"]) == [PROJECT]
    assert payload["grand_total"]["calls"] == len(events)
    assert payload["grand_total"]["in"] == sum(event["input_tokens"] for event in events)
    assert payload["grand_total"]["out"] == sum(event["output_tokens"] for event in events)
    assert payload["grand_total"]["cost"] == pytest.approx(sum(event["cost"] for event in events), abs=1e-6)
