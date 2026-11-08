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
  2. failed attempts — a record with `state` in failed/killed/starting, plus the window's ledger
     spend that no successful run explains.

Attribution: a ledger event belongs to a successful run when its `role` and `model` match that record
and its `ts` falls inside `[record.started, record.started + result.usage.seconds]`. Every other event
of the window is unattributed and is counted in the failed table. `record.started` carries the stamp
`run_role.py` writes (`YYYYmmddTHHMMSSZ`); a ledger `ts` is an ISO 8601 timestamp.

Sources: tokens from `result.usage.prompt_tokens`, `.completion_tokens` and `.cache_read_tokens`; the
gateway column from `result.usage.cost_usd`; an unattributed ledger event from its own
`input_tokens`, `output_tokens`, `cache_read_tokens` and `cost`. A record without a `result.usage`
block contributes zeros. The OpenRouter column is recomputed from the cached catalogue at its USD per
million tokens, rounded to four decimals; cache-read tokens are counted but not priced, because
OpenRouter publishes no cache rate for every route and inventing one would flatter the total. Each
table ends with a line starting with `TOTAL`.

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
import sys
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from .prices_openrouter import CACHE, price_of
from .rig_conf import STATE

FAILED_STATES = ("failed", "killed", "starting")
STAMP_FORMAT = "%Y%m%dT%H%M%SZ"
FIELDS = ("input_tokens", "output_tokens", "cache_read_tokens", "gateway_usd", "openrouter_usd")


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


def _read_records(project: str, since: datetime) -> list[dict]:
    """Records of one project whose `started` stamp falls inside the window.

    Returns:
        The parsed `record.json` bodies under `<state>/<project>/roles/<role>/<stamp>/`, in no
        particular order; directories whose name is not a stamp are not runs and are skipped.
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
            records.append(json.loads(record_path.read_text(encoding="utf-8")))
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


def _finish(rows: Iterable[dict]) -> list[dict]:
    return sorted(rows, key=lambda row: (row["role"], row["model"]))


def _totals(rows: list[dict]) -> dict:
    return {field: round(sum(row[field] for row in rows), 4) for field in FIELDS}


def _attributed(event: dict, moment: datetime, successful: list[tuple[dict, datetime, datetime]]) -> bool:
    """Whether a ledger event belongs to a successful run: same role and model, `ts` inside the run.

    A record's window is `[record.started, record.started + result.usage.seconds]`; the stamp and the
    ledger `ts` are both converted to aware UTC before the comparison (see `_stamp_moment` and
    `_iso_moment`).
    """
    for record, start, end in successful:
        if (
            record.get("role") == event.get("role")
            and record.get("model") == event.get("model")
            and start <= moment <= end
        ):
            return True
    return False


def collect(project: str, days: int) -> dict[str, Any]:
    """Collect the two cost tables of one project's window.

    Returns:
        The payload of the report: the successful rows, the failed rows, and their totals, ordered by
        role and then by model.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    models: dict = json.loads(CACHE.read_text(encoding="utf-8"))["models"]

    successful: list[tuple[dict, datetime, datetime]] = []
    failed_records: list[dict] = []
    for record in _read_records(project, since):
        usage = _usage_of(record)
        if record.get("state") == "done" and usage:
            started = _stamp_moment(record.get("started") or "") or since
            end = started + timedelta(seconds=float(usage.get("seconds") or 0))
            successful.append((record, started, end))
        else:
            failed_records.append(record)

    ok_rows: dict[tuple[str, str], dict] = {}
    for record, _start, _end in successful:
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

    failed_rows: dict[tuple[str, str], dict] = {}
    for record in failed_records:
        usage = _usage_of(record)
        _absorb(
            failed_rows,
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
    for event in _read_events(project, since):
        moment = _iso_moment(event.get("ts"))
        if moment is not None and _attributed(event, moment, successful):
            continue
        _absorb(
            failed_rows,
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

    ok = _finish(ok_rows.values())
    failed = _finish(failed_rows.values())
    return {"ok": ok, "failed": failed, "totals": {"ok": _totals(ok), "failed": _totals(failed)}}


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
