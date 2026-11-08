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


def main() -> int:
    """Print the two tables of one project's window, or the JSON form of the same numbers.

    Returns:
        The exit code: 0 when the report was printed, 1 when the OpenRouter catalogue cache is
        missing (the message goes to stderr), 2 when the command line is wrong.

    Raises:
        NotImplementedError: until the report is implemented.
    """
    raise NotImplementedError
