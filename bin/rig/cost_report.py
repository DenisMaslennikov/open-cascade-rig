#!/usr/bin/env python3
"""cost_report.py — what one project's runs cost, in the gateway's prices and in OpenRouter's list.

A run record carries the cost the gateway billed (`result.usage.cost_usd`), and the gateway's overrides
are 30-70x the public list price. The same tokens repriced against the cached OpenRouter catalogue
answer the owner's question — "what did this project cost in the currency I budget in" — without a
second run.

Data read, under `<XDG_STATE_HOME>/cascade`:
  <slug>/roles/<role>/<ts>/record.json   the run records
  <slug>/usage.jsonl                     the ledger, one event per model call
  pricing-openrouter.json                the cached catalogue, refreshed by prices_openrouter

Both tables carry the same columns: role | model | input tokens | output tokens | cache-read tokens |
cost in gateway prices (USD) | cost in OpenRouter list prices (USD):

  1. successful runs — a record with `state == "done"` and a `result.usage` block;
  2. failed attempts — everything else that is not a live run: the failed/killed/out-of-scope records,
     a `starting`/`running` record whose pid is gone, plus the window's ledger spend that no run
     explains.

A run in progress — a record in `starting`/`running` whose pid the OS still holds — is neither a
success nor a failure: it lands in neither table and the tool says on stderr that it skipped it,
naming the run directory and the pid. A pid alone can lie, because the OS reuses them, so the pid
counts as the run's own process only when it started at or after the record's `started` stamp (a
couple of seconds of tolerance absorb the gap between spawning the process and writing the record):
on Linux the process start is read from `/proc/<pid>/stat` (field 22, clock ticks since boot) and
`/proc/uptime` gives the boot moment, so `now - (uptime - starttime/CLK_TCK)` is the start on the
wall clock. Where `/proc` is unreadable the answer falls back to the pid check alone.

Attribution: a run is counted from one source, so no spend is counted twice. A ledger event belongs to
the run whose `role` and `model` match that record and whose `ts` falls inside
`[record.started, record.started + result.usage.seconds]`; when several runs qualify, the first wins.
A successful run is counted from its record and its own events are skipped. A failed run is counted
from the events of its window, or from `result.usage` only when the window holds no event for that run
at all (a hand-killed run often leaves no ledger line). Every other event of the window belongs to no
record and is counted in the failed table — that spend is real and unattributed. `record.started`
carries the stamp `run_role.py` writes (`YYYYmmddTHHMMSSZ`); a ledger `ts` is an ISO 8601 timestamp.

Sources: tokens from `result.usage.prompt_tokens`, `.completion_tokens` and `.cache_read_tokens`; the
gateway column from `result.usage.cost_usd`; a ledger event from its own `input_tokens`,
`output_tokens`, `cache_read_tokens` and `cost`. A record without a `result.usage` block contributes
zeros. The OpenRouter column is recomputed from the cached catalogue at its USD per million tokens,
rounded to four decimals; cache-read tokens are counted but not priced, because OpenRouter publishes
no cache rate for every route and inventing one would flatter the total. Each table ends with a line
starting with `TOTAL`.

CLI:
  cost_report.py --project <slug> [--days N] [--json]

`--json` prints
  {"ok": [...], "failed": [...], "totals": {"ok": {...}, "failed": {...}}, "prices": "OpenRouter",
   "since": "<iso>"}
where a row is
  {"role", "model", "input_tokens", "output_tokens", "cache_read_tokens", "gateway_usd",
   "openrouter_usd"}
and a totals block sums those five numeric fields over its own table. Both lists are ordered by `role`
and then by `model`, so the same data always prints in the same order. `since` is the ISO timestamp the
window starts at.

Exit 0 — the report was printed, 1 — the catalogue cache is missing, so there are no list prices to
report, 2 — usage.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .prices_openrouter import CACHE, price_of
from .rig_conf import STATE

LIVE_STATES = ("starting", "running")
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "gateway_usd", "openrouter_usd")
# The gap between spawning the run's process and writing its record: a pid is the run's own process
# when it started up to this much after the record's `started` stamp.
START_TOLERANCE = timedelta(seconds=2)


def build_parser() -> argparse.ArgumentParser:
    """Build the command line of the report.

    Returns:
        The parser: `--project` is required, `--days` defaults to seven, `--json` selects the
        machine-readable report.
    """
    parser = argparse.ArgumentParser(
        prog="rig.cost_report",
        description="Cost of one project's runs in gateway prices and in OpenRouter list prices.",
    )
    parser.add_argument("--project", required=True, help="project slug under the cascade state root")
    parser.add_argument("--days", type=int, default=7, help="window in days; default 7")
    parser.add_argument("--json", action="store_true", help="machine-readable report")
    return parser


def _stamp_moment(stamp: str) -> datetime | None:
    """Parse the `YYYYmmddTHHMMSSZ` stamp `run_role.py` writes into a UTC moment.

    Returns:
        The aware UTC moment, or None when the stamp does not match — run directories of card numbers
        (`191f`) carry no timestamp and are skipped.
    """
    try:
        return datetime.strptime(stamp, STAMP_FORMAT).replace(tzinfo=UTC)
    except (TypeError, ValueError):
        return None


def _iso_moment(ts: str) -> datetime | None:
    """Parse a ledger `ts` (ISO 8601) into a UTC moment.

    The ledger timestamps come from `datetime.isoformat` and carry an offset; a naive value is read
    as UTC. Both this and `_stamp_moment` convert to aware UTC so a stamp and a `ts` can be compared
    directly.
    """
    try:
        moment = datetime.fromisoformat(ts)
    except (TypeError, ValueError):
        return None
    return moment.replace(tzinfo=UTC) if moment.tzinfo is None else moment.astimezone(UTC)


def _proc_start_moment(pid: int) -> datetime | None:
    """The moment the process started, on the wall clock, or None when `/proc` does not tell.

    Linux only: field 22 of `/proc/<pid>/stat` (the first field after the parenthesised command, or
    20 zero-based fields after the closing parenthesis) is the start in clock ticks since boot, and
    `/proc/uptime` measures the same boot, so `now - (uptime - starttime / CLK_TCK)` is the start on
    the wall clock. Reading `/proc` in pieces guards against a command name that embeds `)` or
    spaces and against a stat line the kernel cut short.
    """
    try:
        uptime = float(Path("/proc/uptime").read_text(encoding="ascii").split()[0])
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    except (OSError, ValueError, IndexError):
        return None
    fields_after_command = stat[stat.rfind(")") + 1 :].split()
    if len(fields_after_command) < 20:
        return None
    try:
        starttime = int(fields_after_command[19])
        clk_tck = os.sysconf("SC_CLK_TCK")
    except (ValueError, OSError):
        return None
    if clk_tck <= 0:
        return None
    return datetime.now(UTC) - timedelta(seconds=uptime - starttime / clk_tck)


def _alive_since(pid: Any, started: datetime | None) -> bool:
    """Whether a process with that pid exists and is old enough to be the run's own.

    The existence test is the same one `run_records_reconcile.py` uses in the state directory:
    `os.kill(pid, 0)` succeeds for a live process and fails with `OSError` for a gone one. A live
    pid is accepted only when the process started at or after `started` (within the tolerance of
    `START_TOLERANCE`), because the OS reuses pids and a dead run's pid may name a younger process.
    A process that started before the record's stamp cannot be the run either. When `started` is
    unknown or `/proc` is unreadable, the pid check alone decides.
    """
    if not pid:
        return False
    try:
        os.kill(int(pid), 0)
    except (OSError, ValueError):
        return False
    if started is None:
        return True
    start = _proc_start_moment(int(pid))
    if start is None:
        return True
    return start <= started + START_TOLERANCE


def _is_live_run(record: dict) -> bool:
    """Whether the record's run is still going: a live state and a pid that is the run's own."""
    started = _stamp_moment(record.get("started") or "")
    return record.get("state") in LIVE_STATES and _alive_since(record.get("pid"), started)


def _read_records(project: str, since: datetime) -> list[tuple[dict, Path]]:
    """Records of one project whose `started` stamp falls inside the window, with their run directory.

    Returns:
        `(record, run_dir)` pairs of the parsed `record.json` bodies under
        `<state>/<project>/roles/<role>/<stamp>/`, in no particular order; directories whose name is
        not a stamp are not runs and are skipped.
    """
    roles_dir = STATE / project / "roles"
    if not roles_dir.is_dir():
        return []
    records = []
    for record_path in sorted(roles_dir.glob("*/*/record.json")):
        stamp_moment = _stamp_moment(record_path.parent.name)
        if stamp_moment is None or stamp_moment < since:
            continue
        try:
            records.append((json.loads(record_path.read_text(encoding="utf-8")), record_path.parent))
        except (json.JSONDecodeError, OSError):
            continue
    return records


def _read_events(project: str, since: datetime) -> list[dict]:
    """Ledger events of one project whose `ts` falls inside the window."""
    ledger = STATE / project / "usage.jsonl"
    if not ledger.exists():
        return []
    events = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        moment = _iso_moment(event.get("ts"))
        if moment is None or moment < since:
            continue
        events.append(event)
    return events


def _usage_of(record: dict) -> dict:
    """The `result.usage` block of a record, empty when the record carries none."""
    return ((record.get("result") or {}).get("usage") or {}) if isinstance(record.get("result"), dict) else {}


def _new_row(role: str, model: str) -> dict:
    return {
        "role": role,
        "model": model,
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_read_tokens": 0,
        "gateway_usd": 0.0,
        "openrouter_usd": 0.0,
    }


def _openrouter_cost(models: dict, model: str, input_tokens: int, output_tokens: int) -> float:
    """The same tokens at the cached OpenRouter list price, in USD per million tokens.

    Returns:
        The cost rounded to four decimals; 0.0 when the catalogue has no price for the model (its
        aliases included), so an unpriced model never invents a number.
    """
    price = price_of(models, model)
    if price is None:
        return 0.0
    return round((input_tokens * price["in"] + output_tokens * price["out"]) / 1e6, 4)


def _absorb(rows: dict[tuple[str, str], dict], models: dict, role: str, model: str, tokens: dict) -> dict:
    """Add one model call's tokens to the row of `role` + `model` and return the row."""
    row = rows.setdefault((role, model), _new_row(role, model))
    row["input_tokens"] += int(tokens.get("input_tokens") or 0)
    row["output_tokens"] += int(tokens.get("output_tokens") or 0)
    row["cache_read_tokens"] += int(tokens.get("cache_read_tokens") or 0)
    row["gateway_usd"] += float(tokens.get("gateway_usd") or 0.0)
    row["openrouter_usd"] = round(
        row["openrouter_usd"]
        + _openrouter_cost(models, model, int(tokens.get("input_tokens") or 0), int(tokens.get("output_tokens") or 0)),
        4,
    )
    return row


def _absorb_event(rows: dict[tuple[str, str], dict], models: dict, event: dict) -> None:
    """Add one ledger event to the row of its role + model; the event names its cost `cost`."""
    _absorb(
        rows,
        models,
        str(event.get("role") or "?"),
        str(event.get("model") or "?"),
        {
            "input_tokens": event.get("input_tokens"),
            "output_tokens": event.get("output_tokens"),
            "cache_read_tokens": event.get("cache_read_tokens"),
            "gateway_usd": event.get("cost"),
        },
    )


def _finish(rows: Iterable[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (row["role"], row["model"]))


def _totals(rows: list[dict]) -> dict:
    return {field: round(sum(row[field] for row in rows), 4) for field in FIELDS}


def _attributed(event: dict, moment: datetime, runs: list[tuple[dict, datetime, datetime]]) -> bool:
    """Whether a ledger event belongs to a run: same role and model, `ts` inside the run's window.

    A record's window is `[record.started, record.started + result.usage.seconds]`; the stamp and the
    ledger `ts` are both converted to aware UTC before the comparison (see `_stamp_moment` and
    `_iso_moment`). When several runs match, the first wins, so an event is claimed by one run only.
    """
    for _record, start, end in runs:
        if (
            _record.get("role") == event.get("role")
            and _record.get("model") == event.get("model")
            and start <= moment <= end
        ):
            return True
    return False


def collect(project: str, days: int) -> dict[str, Any]:
    """Collect the two cost tables of one project's window.

    Returns:
        The payload of the report: the successful rows, the failed rows, and their totals, ordered by
        role and then by model, plus `skipped` — one stderr line per run in progress the window
        holds, for the caller to print and to keep out of the payload it shows.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    models: dict = json.loads(CACHE.read_text(encoding="utf-8"))["models"]

    runs: list[tuple[dict, datetime, datetime]] = []
    failed_records: list[dict] = []
    skipped: list[str] = []
    for record, run_dir in _read_records(project, since):
        usage = _usage_of(record)
        if record.get("state") == "done" and usage:
            started = _stamp_moment(record.get("started") or "") or since
            end = started + timedelta(seconds=float(usage.get("seconds") or 0))
            runs.append((record, started, end))
        elif _is_live_run(record):
            skipped.append(f"skipped a run in progress: {run_dir} (pid {record.get('pid')})")
        else:
            failed_records.append(record)

    ok_rows: dict[tuple[str, str], dict] = {}
    for record, _start, _end in runs:
        usage = _usage_of(record)
        row = _absorb(
            ok_rows,
            models,
            str(record.get("role") or "?"),
            str(record.get("model") or "?"),
            {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cache_read_tokens": usage.get("cache_read_tokens"),
                "gateway_usd": usage.get("cost_usd"),
            },
        )
        row["gateway_usd"] = round(row["gateway_usd"], 4)

    def event_tokens(event: dict) -> dict:
        return {
            "input_tokens": event.get("input_tokens"),
            "output_tokens": event.get("output_tokens"),
            "cache_read_tokens": event.get("cache_read_tokens"),
            "gateway_usd": event.get("cost"),
        }

    def _absorb_failed(role: str, model: str, tokens: dict) -> None:
        _absorb(failed_rows, models, role, model, tokens)

    events = _read_events(project, since)
    moments = [_iso_moment(event.get("ts")) for event in events]

    failed_rows: dict[tuple[str, str], dict] = {}
    claimed: set[tuple[str, str, datetime]] = set()

    for record in failed_records:
        usage = _usage_of(record)
        role = str(record.get("role") or "?")
        model = str(record.get("model") or "?")
        started = _stamp_moment(record.get("started") or "") or since
        end = started + timedelta(seconds=float(usage.get("seconds") or 0))
        if usage:
            # The run's own events first: the window is the ledger's answer for a failed run, the
            # record's usage is the fallback for when the run left no ledger line at all.
            has_window_event = any(
                _attributed(event, moment, [(record, started, end)])
                for event, moment in zip(events, moments, strict=True)
            )
            if has_window_event:
                continue
            has_role_model_event = any(
                event.get("role") == record.get("role") and event.get("model") == record.get("model")
                for event in events
            )
            if has_role_model_event:
                continue
        _absorb(
            failed_rows,
            models,
            role,
            model,
            {
                "input_tokens": usage.get("prompt_tokens"),
                "output_tokens": usage.get("completion_tokens"),
                "cache_read_tokens": usage.get("cache_read_tokens"),
                "gateway_usd": usage.get("cost_usd"),
            },
        )

    for event, moment in zip(events, moments, strict=True):
        if moment is not None and _attributed(event, moment, runs):
            continue
        if moment is None:
            _absorb_event(failed_rows, models, event)
            continue
        role = str(event.get("role") or "?")
        model = str(event.get("model") or "?")
        matched = False
        for record, start, end in runs:
            if record.get("role") != event.get("role") or record.get("model") != event.get("model"):
                continue
            if not start <= moment <= end:
                continue
            matched = True
            if record.get("state") == "done":
                break
            if (role, model, start) not in claimed:
                claimed.add((role, model, start))
                _absorb_event(failed_rows, models, event)
            break
        if not matched:
            _absorb_event(failed_rows, models, event)

    ok = _finish(ok_rows.values())
    failed = _finish(failed_rows.values())
    return {"ok": ok, "failed": failed, "totals": {"ok": _totals(ok), "failed": _totals(failed)}, "skipped": skipped}


def _print_table(title: str, rows: list[dict], totals: dict) -> None:
    print(f"{title}  role | model | input | output | cache-read | gateway $ | openrouter $")
    for row in rows:
        print(
            f"  {row['role']:16} {row['model'][:36]:36} {row['input_tokens']:10} {row['output_tokens']:8} "
            f"{row['cache_read_tokens']:8} {row['gateway_usd']:12.4f} {row['openrouter_usd']:12.4f}"
        )
    print(
        f"  TOTAL{'':16} {'':36} {totals['input_tokens']:10} {totals['output_tokens']:8} "
        f"{totals['cache_read_tokens']:8} {totals['gateway_usd']:12.4f} {totals['openrouter_usd']:12.4f}"
    )


def main() -> int:
    """Print the two tables of one project's window, or the JSON form of the same numbers.

    Returns:
        The exit code: 0 when the report was printed, 1 when the OpenRouter catalogue cache is
        missing (the message goes to stderr), 2 when the command line is wrong.
    """
    args = build_parser().parse_args()
    if not CACHE.exists():
        print(
            f"ERROR: no OpenRouter catalogue cache at {CACHE}; run prices_openrouter.py --refresh first",
            file=sys.stderr,
        )
        return 1
    report = collect(args.project, args.days)
    for note in report.pop("skipped"):
        print(note, file=sys.stderr)
    report.update(
        {"prices": "OpenRouter", "since": (datetime.now(UTC) - timedelta(days=args.days)).isoformat(timespec="seconds")}
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 0
    _print_table("successful runs", report["ok"], report["totals"]["ok"])
    print()
    _print_table("failed attempts", report["failed"], report["totals"]["failed"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
