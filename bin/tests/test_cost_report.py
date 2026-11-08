"""The cost report prices a project's runs twice and says which number is which.

`run_role.py` writes `roles/<role>/<ts>/record.json` with `result.usage.cost_usd` in gateway currency
and appends every model call to `usage.jsonl`. The report puts the two side by side: the record's own
cost and the same tokens repriced at the cached OpenRouter catalogue. A swap of the two columns, a
failed attempt swallowed by "successful runs only", a stray ledger event nobody owns, or a total that
is not the sum of its rows is what these tests exist to catch.

The tool is exercised as an operator runs it — `python3 -m rig.cost_report` from `bin` — because the
state root comes from `XDG_STATE_HOME`, and a module-level constant computed at import time is exactly
what a test that patches attributes would miss.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rig import config_init

SLUG = "demo"
MODEL = "deepseek/deepseek-v4-pro"

# USD per million tokens, as the OpenRouter catalogue cache stores them.
CATALOGUE_IN = 0.27
CATALOGUE_OUT = 1.1


def _bin_dir() -> Path:
    return Path(config_init.__file__).resolve().parent.parent


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _stamp(moment: datetime) -> str:
    return moment.strftime("%Y%m%dT%H%M%SZ")


def _done(
    role: str,
    started: datetime,
    prompt: int,
    completion: int,
    cache_read: int,
    cost_usd: float,
    seconds: int,
) -> dict:
    return {
        "role": role,
        "state": "done",
        "started": _stamp(started),
        "model": MODEL,
        "result": {
            "ok": True,
            "role": role,
            "model": MODEL,
            "usage": {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "cache_read_tokens": cache_read,
                "cost_usd": cost_usd,
                "seconds": seconds,
            },
        },
    }


def _write_record(state_root: Path, record: dict) -> Path:
    path = state_root / "cascade" / SLUG / "roles" / record["role"] / record["started"] / "record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _write_events(state_root: Path, events: list[dict]) -> None:
    path = state_root / "cascade" / SLUG / "usage.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")


def _run(state_root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "XDG_STATE_HOME": str(state_root)}
    return subprocess.run(
        [sys.executable, "-m", "rig.cost_report", *args],
        cwd=_bin_dir(),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def _report_json(state_root: Path) -> dict:
    proc = _run(state_root, "--project", SLUG, "--days", "7", "--json")
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@pytest.fixture
def now() -> datetime:
    return datetime.now(UTC)


@pytest.fixture
def state_root(tmp_path: Path) -> Path:
    """A cascade state root holding only the OpenRouter catalogue cache."""
    root = tmp_path / "state"
    cache = root / "cascade" / "pricing-openrouter.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(
        json.dumps(
            {
                "fetched_at": "2026-01-01T00:00:00+00:00",
                "source": "test",
                "models": {MODEL: {"in": CATALOGUE_IN, "out": CATALOGUE_OUT, "name": "DeepSeek V4 Pro"}},
            }
        ),
        encoding="utf-8",
    )
    return root


@pytest.fixture
def scenario(state_root: Path, now: datetime) -> dict:
    """Two successful runs, one killed record without usage, one ledger event no run explains."""
    executor_start = now - timedelta(hours=3)
    reviewer_start = now - timedelta(hours=1)
    _write_record(state_root, _done("executor", executor_start, 100_000, 10_000, 20_000, 3.5, 3600))
    _write_record(state_root, _done("reviewer", reviewer_start, 50_000, 5_000, 0, 1.75, 600))
    _write_record(
        state_root,
        {
            "role": "reviewer",
            "state": "killed",
            "started": _stamp(executor_start + timedelta(minutes=5)),
            "model": MODEL,
        },
    )
    _write_events(
        state_root,
        [
            # Inside the successful executor run's window, same role and model: attributed to it,
            # so it must not reach the failed table.
            {
                "ts": _iso(executor_start + timedelta(minutes=10)),
                "role": "executor",
                "model": MODEL,
                "input_tokens": 90_000,
                "output_tokens": 9_000,
                "cache_read_tokens": 18_000,
                "cost": 3.25,
            },
            # Same role and model as that run, one hour before it started: outside the window, so the
            # spend belongs to nobody and lands in the failed table.
            {
                "ts": _iso(executor_start - timedelta(hours=1)),
                "role": "executor",
                "model": MODEL,
                "input_tokens": 4000,
                "output_tokens": 200,
                "cache_read_tokens": 0,
                "cost": 1.25,
            },
        ],
    )
    return {"executor_start": executor_start, "reviewer_start": reviewer_start}


def test_a_done_run_lands_in_ok_and_the_killed_record_in_failed(state_root, scenario):
    payload = _report_json(state_root)

    assert [row["role"] for row in payload["ok"]] == ["executor", "reviewer"]
    assert [row["role"] for row in payload["failed"]] == ["executor", "reviewer"]


def test_a_successful_run_reports_the_tokens_of_its_record(state_root, scenario):
    row = next(row for row in _report_json(state_root)["ok"] if row["role"] == "executor")

    assert row["model"] == MODEL
    assert row["input_tokens"] == 100_000
    assert row["output_tokens"] == 10_000
    assert row["cache_read_tokens"] == 20_000


def test_ledger_spend_attributed_to_a_run_is_not_counted_a_second_time(state_root, scenario):
    payload = _report_json(state_root)

    executor_ok = next(row for row in payload["ok"] if row["role"] == "executor")
    assert executor_ok["input_tokens"] == 100_000
    assert executor_ok["gateway_usd"] == pytest.approx(3.5)

    # The attributed event (90k input, $3.25) is invisible; only the stray one is in the failed table.
    assert payload["totals"]["failed"]["gateway_usd"] == pytest.approx(1.25)
    assert payload["totals"]["failed"]["input_tokens"] == 4000


def test_the_gateway_column_is_the_record_and_the_openrouter_column_is_recomputed(state_root, scenario):
    row = next(row for row in _report_json(state_root)["ok"] if row["role"] == "executor")

    assert row["gateway_usd"] == pytest.approx(3.5)
    assert row["openrouter_usd"] == pytest.approx(0.038, abs=1e-4)
    assert row["gateway_usd"] != pytest.approx(row["openrouter_usd"])


def test_a_killed_record_without_usage_contributes_a_zero_row(state_root, scenario):
    row = next(row for row in _report_json(state_root)["failed"] if row["role"] == "reviewer")

    assert row["model"] == MODEL
    assert row["input_tokens"] == 0
    assert row["output_tokens"] == 0
    assert row["gateway_usd"] == pytest.approx(0.0)


def test_ledger_spend_no_run_explains_lands_in_the_failed_table(state_root, scenario):
    row = next(row for row in _report_json(state_root)["failed"] if row["role"] == "executor")

    assert row["model"] == MODEL
    assert row["input_tokens"] == 4000
    assert row["output_tokens"] == 200
    assert row["gateway_usd"] == pytest.approx(1.25)
    assert row["openrouter_usd"] == pytest.approx(0.0013, abs=1e-4)


def test_each_total_is_the_sum_of_the_rows_of_its_table(state_root, scenario):
    payload = _report_json(state_root)

    for table in ("ok", "failed"):
        rows = payload[table]
        totals = payload["totals"][table]
        for field in ("input_tokens", "output_tokens", "cache_read_tokens"):
            assert totals[field] == sum(row[field] for row in rows)
        for field in ("gateway_usd", "openrouter_usd"):
            assert totals[field] == pytest.approx(sum(row[field] for row in rows), abs=1e-6)


def test_the_successful_totals_hold_both_runs(state_root, scenario):
    totals = _report_json(state_root)["totals"]["ok"]

    assert totals["input_tokens"] == 150_000
    assert totals["output_tokens"] == 15_000
    assert totals["cache_read_tokens"] == 20_000
    assert totals["gateway_usd"] == pytest.approx(5.25)
    assert totals["openrouter_usd"] == pytest.approx(0.057, abs=1e-4)


def test_the_json_report_has_the_documented_keys_and_a_window_start(state_root, scenario, now):
    payload = _report_json(state_root)

    assert set(payload) == {"ok", "failed", "totals", "prices", "since"}
    assert payload["prices"] == "OpenRouter"
    assert set(payload["totals"]) == {"ok", "failed"}
    for row in payload["ok"] + payload["failed"]:
        assert set(row) == {
            "role",
            "model",
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "gateway_usd",
            "openrouter_usd",
        }
        assert isinstance(row["role"], str)
        assert isinstance(row["model"], str)
        assert isinstance(row["gateway_usd"], int | float)

    window_start = datetime.fromisoformat(payload["since"]).replace(tzinfo=None)
    age = now.replace(tzinfo=None) - window_start
    assert timedelta(days=6) < age < timedelta(days=8)


def test_the_text_report_prints_two_tables_each_ending_with_totals(state_root, scenario):
    proc = _run(state_root, "--project", SLUG, "--days", "7")

    assert proc.returncode == 0, proc.stderr
    lines = proc.stdout.splitlines()
    assert [line.split()[0].upper() for line in lines if line.strip().upper().startswith("TOTAL")] == [
        "TOTAL",
        "TOTAL",
    ]
    lowered = proc.stdout.lower()
    assert "gateway" in lowered
    assert "openrouter" in lowered
    assert "executor" in lowered
    assert "reviewer" in lowered
    # The stray spend is in the failed table and nowhere else: neither successful run costs 1.25.
    assert "1.25" in proc.stdout


def test_a_missing_catalogue_cache_is_an_error_not_a_zero(tmp_path, now):
    state_root = tmp_path / "state"
    _write_record(state_root, _done("executor", now - timedelta(hours=1), 1000, 100, 0, 1.0, 60))
    _write_events(state_root, [])

    proc = _run(state_root, "--project", SLUG, "--days", "7")

    assert proc.returncode == 1
    assert proc.stderr.strip()
    assert "Traceback" not in proc.stderr


def test_a_report_without_a_project_is_a_usage_error(state_root):
    proc = _run(state_root, "--days", "7")

    assert proc.returncode == 2
    assert "--project" in proc.stderr
    assert "Traceback" not in proc.stderr
