#!/usr/bin/env python3
"""prices_openrouter.py — what a run actually costs, priced by OpenRouter, not by the local gateway.

The local gateway prices its routes with overrides that are 30-70x the public list price and are
denominated in a currency that is not the one the owner budgets in. A number like "$93 for a day" is
therefore both wrong and alarming. OpenRouter publishes one price list for the models we actually use,
and it is reachable from this host only through the LAN SOCKS proxy.

So: a weekly job pulls the whole catalogue through the proxy, caches it in the state directory (never in
the repository — it is data, not knowledge), and every report can be repriced against it.

CLI:
  prices_openrouter.py --refresh [--proxy socks5h://192.168.31.6:10808]
  prices_openrouter.py --report [--days 1] [--project P] [--json]

Exit 0 — ok, 1 — the catalogue could not be fetched or parsed.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cascade"
CACHE = STATE / "pricing-openrouter.json"
DEFAULT_PROXY = os.environ.get("CASCADE_PRICES_PROXY", "socks5h://192.168.31.6:10808")
URL = "https://openrouter.ai/api/v1/models"

# Our gateway names are OpenRouter ids with a "~" prefix, so the mapping is mostly identity; the
# exceptions are routes the router renames and keep a dated sibling for.
ALIASES = {
    "~deepseek/deepseek-v4-pro-latest": ["deepseek/deepseek-v4-pro", "deepseek/deepseek-v4-pro-0813"],
    "~anthropic/claude-opus-latest": ["~anthropic/claude-opus-latest", "anthropic/claude-opus-4.5"],
    "~anthropic/claude-sonnet-latest": ["~anthropic/claude-sonnet-latest", "anthropic/claude-sonnet-4.5"],
}


def fetch(proxy: str) -> dict:
    proc = subprocess.run(
        ["curl", "-s", "--max-time", "60", "--socks5-hostname", proxy.replace("socks5h://", ""), URL],
        capture_output=True, text=True)
    if proc.returncode != 0 or not proc.stdout.strip():
        raise SystemExit(f"ERROR: OpenRouter fetch failed via {proxy}: {proc.stderr.strip()[:200]}")
    data = json.loads(proc.stdout)
    prices = {}
    for model in data.get("data") or []:
        pricing = model.get("pricing") or {}
        try:
            prompt = float(pricing.get("prompt") or 0) * 1e6
            completion = float(pricing.get("completion") or 0) * 1e6
        except (TypeError, ValueError):
            continue
        prices[model["id"]] = {"in": round(prompt, 4), "out": round(completion, 4),
                               "name": model.get("name") or model["id"]}
    STATE.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                 "source": URL, "models": prices}, ensure_ascii=False), encoding="utf-8")
    return prices


def load(refresh: bool, proxy: str) -> dict:
    if refresh or not CACHE.exists():
        return fetch(proxy)
    return json.loads(CACHE.read_text(encoding="utf-8"))["models"]


def price_of(models: dict, name: str) -> dict | None:
    for candidate in ALIASES.get(name, [name]):
        if candidate in models:
            return models[candidate]
    return None


def report(models: dict, days: int, project: str | None, as_json: bool = False) -> int:
    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    ledgers = [STATE / project / "usage.jsonl"] if project else sorted(STATE.glob("*/usage.jsonl"))
    rows: dict[str, dict] = {}
    unknown: set[str] = set()
    for ledger in ledgers:
        if not ledger.exists():
            continue
        for line in ledger.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            rec = json.loads(line)
            if str(rec.get("ts") or "") < since:
                continue
            model = rec.get("model") or "?"
            price = price_of(models, model)
            if price is None:
                unknown.add(model)
                continue
            key = f"{rec.get('role') or '?'}|{model}"
            agg = rows.setdefault(key, {"role": rec.get("role"), "model": model,
                                        "in": 0, "out": 0, "cache": 0, "cost": 0.0, "calls": 0})
            agg["in"] += int(rec.get("input_tokens") or 0)
            agg["out"] += int(rec.get("output_tokens") or 0)
            agg["cache"] += int(rec.get("cache_read_tokens") or 0)
            agg["calls"] += 1
            # Cache reads are not priced here: OpenRouter does not publish a cache rate for every
            # route, and inventing one would make the total look better than it is.
            agg["cost"] += (int(rec.get("input_tokens") or 0) * price["in"] / 1e6
                            + int(rec.get("output_tokens") or 0) * price["out"] / 1e6)

    out = sorted(rows.values(), key=lambda r: -r["cost"])
    if unknown:
        print(f"note: no OpenRouter price for {sorted(unknown)}", file=sys.stderr)
    if as_json:
        print(json.dumps({"window_days": days, "rows": out,
                          "total": round(sum(r["cost"] for r in out), 4),
                          "priced_by": "openrouter"}, ensure_ascii=False, indent=1))
        return 0
    print(f"{'role':16} {'model':36} {'calls':>5} {'in':>9} {'out':>8} {'cost, $':>9}")
    for r in out:
        print(f"{r['role'] or '?':16} {r['model'][:36]:36} {r['calls']:5} {r['in']:9} {r['out']:8} {r['cost']:9.2f}")
    print(f"{'TOTAL':16} {'':36} {'':5} {'':9} {'':8} {sum(r['cost'] for r in out):9.2f}"
          f"   (prices: OpenRouter, {days}d window)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenRouter prices and cost reports")
    ap.add_argument("--refresh", action="store_true", help="fetch the catalogue now")
    ap.add_argument("--report", action="store_true", help="reprice the ledger")
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--project")
    ap.add_argument("--proxy", default=DEFAULT_PROXY)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    models = load(args.refresh or args.report, args.proxy)
    if args.report:
        return report(models, args.days, args.project, as_json=args.json)
    if args.refresh:
        print(json.dumps({"fetched": len(models), "cache": str(CACHE)}, ensure_ascii=False))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
