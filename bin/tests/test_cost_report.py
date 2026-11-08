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
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rig import config_init

SLUG = "demo"
MODEL = "deepseek/deepseek-v4-pro"
# The states the tool treats as a run in progress: a set, not one string.
LIVE_STATES = ("starting", "running")

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


def _starting(role: str, started: datetime, pid: int) -> dict:
    """The record `run_role.py` writes when it starts: no usage yet, and the pid of the process."""
    return {
        "role": role,
        "state": "starting",
        "pid": pid,
        "started": _stamp(started),
        "model": MODEL,
    }


def _in_progress(role: str, started: datetime, pid: int, state: str) -> dict:
    """A record of a run that has not finished, in one of the tool's live states."""
    return _starting(role, started, pid) | {"state": state}


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    return True


def _dead_pid() -> int:
    """A pid no process holds, found the way `run_records_reconcile.py` tests for liveness."""
    pid = 4_194_304  # above the default Linux `pid_max`; step up on a machine that raised it
    while _alive(pid):
        pid += 1
    return pid


def _write_record(state_root: Path, record: dict) -> Path:
    path = state_root / "cascade" / SLUG / "roles" / record["role"] / record["started"] / "record.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def _failed(
    role: str,
    started: datetime,
    prompt: int,
    completion: int,
    cache_read: int,
    cost_usd: float,
    seconds: int,
) -> dict:
    """A record of a run that ended unsuccessfully but still reports what the gateway billed."""
    record = _done(role, started, prompt, completion, cache_read, cost_usd, seconds)
    record["state"] = "failed"
    record["result"]["ok"] = False
    return record


def _event(
    ts: datetime,
    role: str,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cost: float,
) -> dict:
    """One model call as `usage.jsonl` records it."""
    return {
        "ts": _iso(ts),
        "role": role,
        "model": MODEL,
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache_read_tokens,
        "cost": cost,
    }


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


@pytest.fixture
def failed_run_with_its_event(state_root: Path, now: datetime) -> dict:
    """A failed record and one ledger event of the same run, inside the run's own window.

    The record claims 100/10 tokens at $3.50 and the event 7000/3000 at $0.25, so a row that adds
    the two together (7100/3010, $3.75) is distinguishable from one that carries the event alone.
    """
    started = now - timedelta(hours=2)
    record = _failed("reviewer", started, 100, 10, 20, 3.5, 600)
    _write_record(state_root, record)
    event = _event(started + timedelta(minutes=5), "reviewer", 7000, 3000, 500, 0.25)
    _write_events(state_root, [event])
    return {"record": record, "event": event}


@pytest.fixture
def failed_run_without_event(state_root: Path, now: datetime) -> dict:
    """A failed record with usage and no ledger line at all, as a hand-killed run often leaves it."""
    record = _failed("reviewer", now - timedelta(hours=2), 100, 10, 20, 3.5, 600)
    _write_record(state_root, record)
    _write_events(state_root, [])
    return {"record": record}


@pytest.fixture
def failed_run_with_two_events(state_root: Path, now: datetime) -> dict:
    """A failed record and two ledger events of the same run, both inside its window."""
    started = now - timedelta(hours=2)
    record = _failed("reviewer", started, 100, 10, 20, 3.5, 3600)
    _write_record(state_root, record)
    events = [
        _event(started + timedelta(minutes=5), "reviewer", 7000, 3000, 500, 0.25),
        _event(started + timedelta(minutes=20), "reviewer", 2000, 1000, 100, 0.10),
    ]
    _write_events(state_root, events)
    return {"record": record, "events": events}


@pytest.fixture
def sleeper() -> Iterator[subprocess.Popen]:
    """A process of this test's own that is still running, and that started just now."""
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
    try:
        yield process
    finally:
        process.terminate()
        process.wait(timeout=10)


@pytest.fixture
def live_run(state_root: Path, sleeper: subprocess.Popen) -> dict:
    """A `starting` record stamped as it starts, whose pid is a child process that started with it."""
    record = _starting("specifier", datetime.now(UTC), sleeper.pid)
    _write_record(state_root, record)
    return record


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


def test_a_failed_run_with_its_ledger_event_reports_the_event_not_the_record(state_root, failed_run_with_its_event):
    row = next(row for row in _report_json(state_root)["failed"] if row["role"] == "reviewer")

    assert row["input_tokens"] == 7000
    assert row["output_tokens"] == 3000
    assert row["cache_read_tokens"] == 500
    assert row["gateway_usd"] == pytest.approx(0.25)
    assert row["openrouter_usd"] == pytest.approx(0.0052, abs=1e-4)


def test_a_failed_run_without_a_ledger_event_reports_the_usage_of_its_record(state_root, failed_run_without_event):
    row = next(row for row in _report_json(state_root)["failed"] if row["role"] == "reviewer")

    assert row["input_tokens"] == 100
    assert row["output_tokens"] == 10
    assert row["cache_read_tokens"] == 20
    assert row["gateway_usd"] == pytest.approx(3.5)


def test_two_ledger_events_of_one_failed_run_are_summed_without_the_record(state_root, failed_run_with_two_events):
    row = next(row for row in _report_json(state_root)["failed"] if row["role"] == "reviewer")

    assert row["input_tokens"] == 9000
    assert row["output_tokens"] == 4000
    assert row["cache_read_tokens"] == 600
    assert row["gateway_usd"] == pytest.approx(0.35)


def test_the_failed_total_stays_the_sum_of_its_rows_when_a_run_has_its_own_event(state_root, failed_run_with_its_event):
    payload = _report_json(state_root)

    for table in ("ok", "failed"):
        rows = payload[table]
        totals = payload["totals"][table]
        for field in ("input_tokens", "output_tokens", "cache_read_tokens"):
            assert totals[field] == sum(row[field] for row in rows)
        for field in ("gateway_usd", "openrouter_usd"):
            assert totals[field] == pytest.approx(sum(row[field] for row in rows), abs=1e-6)


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


def test_a_run_still_in_progress_is_in_neither_table(state_root, live_run):
    payload = _report_json(state_root)

    assert payload["ok"] == []
    assert payload["failed"] == []
    assert payload["totals"]["failed"]["gateway_usd"] == pytest.approx(0.0)
    assert payload["totals"]["failed"]["input_tokens"] == 0


def test_the_report_says_on_stderr_that_it_skipped_a_run_in_progress(state_root, live_run):
    proc = _run(state_root, "--project", SLUG, "--days", "7")

    assert proc.returncode == 0, proc.stderr
    assert proc.stderr.strip()
    assert "skip" in proc.stderr.lower()
    assert str(live_run["pid"]) in proc.stderr or live_run["started"] in proc.stderr


def test_a_starting_record_whose_process_is_gone_is_still_a_failed_attempt(state_root, now):
    _write_record(state_root, _starting("specifier", now - timedelta(hours=2), _dead_pid()))

    payload = _report_json(state_root)

    assert payload["ok"] == []
    assert [row["role"] for row in payload["failed"]] == ["specifier"]


@pytest.mark.parametrize("state", ["failed", "killed", "out of scope"])
def test_a_finished_unsuccessful_record_stays_in_the_failed_table(state_root, now, state):
    _write_record(
        state_root,
        {
            "role": "reviewer",
            "state": state,
            "started": _stamp(now - timedelta(hours=2)),
            "model": MODEL,
        },
    )

    payload = _report_json(state_root)

    assert payload["ok"] == []
    assert [row["role"] for row in payload["failed"]] == ["reviewer"]


@pytest.mark.parametrize("state", LIVE_STATES)
def test_a_record_whose_pid_was_reused_after_the_run_died_is_a_failed_attempt(state_root, now, sleeper, state):
    """A process that started an hour after the record did is not the run: its spend stays in the table."""
    _write_record(state_root, _in_progress("specifier", now - timedelta(hours=1), sleeper.pid, state))

    proc = _run(state_root, "--project", SLUG, "--days", "7", "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["ok"] == []
    assert [row["role"] for row in payload["failed"]] == ["specifier"]
    assert "skip" not in proc.stderr.lower()


@pytest.mark.parametrize("state", LIVE_STATES)
def test_a_record_whose_pid_was_reused_does_not_lose_its_spend(state_root, now, sleeper, state):
    """The spend of a dead run stays in the failed table even when the record carries no ledger line."""
    record = _in_progress("specifier", now - timedelta(hours=1), sleeper.pid, state) | {
        "result": {"usage": {"prompt_tokens": 100, "completion_tokens": 10, "cost_usd": 3.5, "seconds": 600}}
    }
    _write_record(state_root, record)
    _write_events(state_root, [])

    failed = _report_json(state_root)["failed"]

    assert [row["role"] for row in failed] == ["specifier"]
    assert failed[0]["input_tokens"] == 100
    assert failed[0]["output_tokens"] == 10
    assert failed[0]["gateway_usd"] == pytest.approx(3.5)


@pytest.mark.parametrize("state", LIVE_STATES)
def test_a_run_in_progress_whose_process_started_with_its_record_is_in_neither_table(state_root, sleeper, state):
    _write_record(state_root, _in_progress("specifier", datetime.now(UTC), sleeper.pid, state))

    payload = _report_json(state_root)

    assert payload["ok"] == []
    assert payload["failed"] == []
    assert payload["totals"]["failed"]["gateway_usd"] == pytest.approx(0.0)
    assert payload["totals"]["failed"]["input_tokens"] == 0


@pytest.mark.parametrize("state", LIVE_STATES)
def test_the_skip_message_names_the_run_directory_or_the_pid_of_a_live_run(state_root, sleeper, state):
    started = datetime.now(UTC)
    run_dir = _write_record(state_root, _in_progress("specifier", started, sleeper.pid, state))

    proc = _run(state_root, "--project", SLUG, "--days", "7")

    assert proc.returncode == 0, proc.stderr
    assert "skip" in proc.stderr.lower()
    assert str(sleeper.pid) in proc.stderr or str(run_dir) in proc.stderr


@pytest.mark.parametrize("state", LIVE_STATES)
@pytest.mark.parametrize(
    "age",
    [pytest.param(timedelta(0), id="stamped-now"), pytest.param(timedelta(hours=2), id="stamped-two-hours-ago")],
)
def test_a_live_state_record_whose_pid_is_dead_is_a_failed_attempt_whatever_its_stamp_says(state_root, now, state, age):
    _write_record(state_root, _in_progress("specifier", now - age, _dead_pid(), state))

    payload = _report_json(state_root)

    assert payload["ok"] == []
    assert [row["role"] for row in payload["failed"]] == ["specifier"]
