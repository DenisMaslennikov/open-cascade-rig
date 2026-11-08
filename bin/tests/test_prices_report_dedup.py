"""A ledger reached through two paths must be counted once, and a single project must not change.

`report()` globs `<state>/*/usage.jsonl`. On the owner's machine one project directory is a symlink to
another (`open-capsuleer-command-center -> occc`), so the glob yields the same file twice and every
event is counted twice: three paths, two distinct files, a weekly total of $26.81 against $15.80 on
non-overlapping paths. These tests freeze the rule the fix must restore — one file, one count — while
the numbers of a single-project report stay exactly what they were.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rig import prices_openrouter

MODEL_A = "vendor/model-a"
MODEL_B = "vendor/model-b"

# USD per million tokens, the shape `prices_openrouter` caches: a model maps to its two rates.
PRICES = {
    MODEL_A: {"in": 10.0, "out": 40.0, "name": "Model A"},
    MODEL_B: {"in": 5.0, "out": 100.0, "name": "Model B"},
}

PROJECT = "occc"
ALIAS = "open-capsuleer-command-center"

# executor: 1000 * 10/1e6 + 500 * 40/1e6 = 0.03; reviewer: 2000 * 5/1e6 + 100 * 100/1e6 = 0.02.
EXPECTED_ROWS = [
    {"role": "executor", "model": MODEL_A, "calls": 1, "in": 1000, "out": 500, "cost": 0.03},
    {"role": "reviewer", "model": MODEL_B, "calls": 1, "in": 2000, "out": 100, "cost": 0.02},
]
EXPECTED_TOTAL_IN = 3000
EXPECTED_TOTAL_OUT = 600
EXPECTED_TOTAL_CALLS = 2
EXPECTED_TOTAL_COST = 0.05


def _recent() -> str:
    return (datetime.now(UTC) - timedelta(minutes=5)).isoformat(timespec="seconds")


def _old() -> str:
    return (datetime.now(UTC) - timedelta(days=30)).isoformat(timespec="seconds")


def _events() -> list[dict]:
    return [
        {
            "ts": _recent(),
            "role": "executor",
            "model": MODEL_A,
            "input_tokens": 1000,
            "output_tokens": 500,
            "cache_read_tokens": 200,
            "cost": 1.0,
        },
        {
            "ts": _recent(),
            "role": "reviewer",
            "model": MODEL_B,
            "input_tokens": 2000,
            "output_tokens": 100,
            "cache_read_tokens": 0,
            "cost": 0.5,
        },
    ]


def _write_ledger(path: Path, events: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _state(monkeypatch, root: Path, alias: str = ALIAS, events: list[dict] | None = None) -> Path:
    """Build a state root whose project ledger is also reachable through a real directory symlink."""
    state = root / "state"
    monkeypatch.setattr(prices_openrouter, "STATE", state)
    _write_ledger(state / PROJECT / "usage.jsonl", _events() if events is None else events)
    (state / alias).symlink_to(state / PROJECT, target_is_directory=True)
    return state


def _report(capsys, project: str | None, days: int = 7) -> str:
    assert prices_openrouter.report(PRICES, days, project) == 0
    return capsys.readouterr().out


def _row(line: str) -> dict | None:
    fields = line.split()
    if len(fields) != 6 or fields[0] in {"role", "TOTAL"}:
        return None
    try:
        return {
            "role": fields[0],
            "model": fields[1],
            "calls": int(fields[2]),
            "in": int(fields[3]),
            "out": int(fields[4]),
            "cost": float(fields[5]),
        }
    except ValueError:
        return None


def _rows(stdout: str) -> list[dict]:
    return [row for line in stdout.splitlines() if (row := _row(line)) is not None]


def _total_line(stdout: str) -> str:
    for line in stdout.splitlines():
        if line.startswith("TOTAL"):
            return line
    raise AssertionError(f"the report has no TOTAL line:\n{stdout}")


def test_a_symlinked_ledger_is_counted_once(monkeypatch, tmp_path, capsys):
    _state(monkeypatch, tmp_path)

    stdout = _report(capsys, project=None)
    rows = _rows(stdout)

    assert rows == EXPECTED_ROWS
    assert sum(row["calls"] for row in rows) == EXPECTED_TOTAL_CALLS
    assert sum(row["in"] for row in rows) == EXPECTED_TOTAL_IN
    assert sum(row["out"] for row in rows) == EXPECTED_TOTAL_OUT
    assert f"{EXPECTED_TOTAL_COST:.2f}" in _total_line(stdout)


def test_the_json_total_is_the_single_counted_total(monkeypatch, tmp_path, capsys):
    _state(monkeypatch, tmp_path)

    assert prices_openrouter.report(PRICES, 7, None, as_json=True) == 0

    payload = json.loads(capsys.readouterr().out)

    assert [(row["role"], row["calls"], row["in"], row["out"]) for row in payload["rows"]] == [
        ("executor", 1, 1000, 500),
        ("reviewer", 1, 2000, 100),
    ]
    assert payload["total"] == pytest.approx(EXPECTED_TOTAL_COST)


@pytest.mark.parametrize("alias", ["aaa-occc", "zzz-occc"])
def test_the_numbers_do_not_depend_on_the_alias_sorting_first_or_last(monkeypatch, tmp_path, capsys, alias):
    _state(monkeypatch, tmp_path, alias=alias)

    assert _rows(_report(capsys, project=None)) == EXPECTED_ROWS


def test_the_row_order_is_the_same_whichever_path_the_glob_sees_first(monkeypatch, tmp_path, capsys):
    orders = []
    for alias in ("aaa-occc", "zzz-occc"):
        _state(monkeypatch, tmp_path / alias, alias=alias)
        orders.append([f"{row['role']}|{row['model']}" for row in _rows(_report(capsys, project=None))])

    assert orders[0] == orders[1]
    assert orders[0] == [f"{row['role']}|{row['model']}" for row in EXPECTED_ROWS]


def test_a_single_project_report_keeps_its_rows_and_its_total(monkeypatch, tmp_path, capsys):
    state = _state(monkeypatch, tmp_path)
    _write_ledger(
        state / "other" / "usage.jsonl",
        [{"ts": _recent(), "role": "archivist", "model": MODEL_A, "input_tokens": 7, "output_tokens": 7}],
    )

    stdout = _report(capsys, project=PROJECT)

    assert _rows(stdout) == EXPECTED_ROWS
    assert f"{EXPECTED_TOTAL_COST:.2f}" in _total_line(stdout)


@pytest.mark.parametrize("project", [None, PROJECT])
def test_an_event_older_than_the_window_is_ignored_on_every_path(monkeypatch, tmp_path, capsys, project):
    stale = {
        "ts": _old(),
        "role": "archivist",
        "model": MODEL_A,
        "input_tokens": 9000,
        "output_tokens": 9000,
        "cache_read_tokens": 0,
    }
    _state(monkeypatch, tmp_path, events=[stale, *_events()])

    assert [row["role"] for row in _rows(_report(capsys, project=project))] == ["executor", "reviewer"]
