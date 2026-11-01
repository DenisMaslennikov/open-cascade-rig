#!/usr/bin/env python3
"""usage.py — учёт токенов и денег каскада: проект → фича → задача.

Данные: append-only JSONL, по одному событию на вызов модели.
  ~/.local/state/cascade/<project>/usage.jsonl

Команды:
  record   --project P --feature F --task T --role R --model M
           --input N --output N [--cache-read N] [--cost X] [--note ...]
           стоимость считается автоматически по кэшу цен, если --cost не задан

  report   [--project P] [--feature F] [--task T] [--all] [--json] [--by model|role]
           сводка: общий итог, по фичам, по задачам, по моделям

  price    [--refresh]      обновить кэш цен из /model/info шлюза
                           (~/.config/cascade/pricing.json — a cache, regenerated from the
                           gateway when missing; for money in the owner's currency use
                           bin/prices_openrouter.py instead)

  projects                  список проектов с данными

Примеры:
  usage.py record -p eve --feature industry -t t2-materials -r executor \
      -m deepseek/deepseek-v4-flash --input 12000 --output 900
  usage.py report -p eve
  usage.py report -p eve --feature industry --json
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.request
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

from rig_conf import gateway, project_config

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state")) / "cascade"
CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cascade"
PRICES = CONFIG / "pricing.json"


# ---------------------------------------------------------------- helpers
def ledger_path(project: str) -> Path:
    return STATE / project / "usage.jsonl"


def load_keys() -> dict[str, str]:
    out: dict[str, str] = {}
    env = CONFIG / "keys.env"
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def load_prices(refresh: bool = False, project: str | None = None) -> dict[str, dict[str, float]]:
    if refresh or not PRICES.exists():
        keys = load_keys()
        key = keys.get("LITELLM_KEY") or next(iter(keys.values()), "")
        base = gateway(project_config(project) if project else {}, script="usage price")
        req = urllib.request.Request(base + "/model/info")
        req.add_header("Authorization", "Bearer " + key)
        with urllib.request.urlopen(req, timeout=30) as r:
            data = json.load(r)["data"]
        prices = {}
        for m in data:
            name, info = m.get("model_name"), m.get("model_info", {})
            if not name:
                continue
            prices[name] = {
                "in": float(info.get("input_cost_per_token") or 0.0),
                "out": float(info.get("output_cost_per_token") or 0.0),
            }
        PRICES.parent.mkdir(parents=True, exist_ok=True)
        PRICES.write_text(json.dumps(prices, indent=1), encoding="utf-8")
        return prices
    return json.loads(PRICES.read_text(encoding="utf-8"))


def cost_of(model: str, tin: int, tout: int, prices: dict[str, dict[str, float]]) -> float:
    p = prices.get(model) or {}
    return round(tin * p.get("in", 0.0) + tout * p.get("out", 0.0), 6)


def read_events(project: str) -> list[dict]:
    path = ledger_path(project)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(json.loads(line))
    return out


# ---------------------------------------------------------------- commands
def cmd_record(a: argparse.Namespace) -> int:
    prices = {} if a.cost is not None else load_prices(project=a.project)
    cost = a.cost if a.cost is not None else cost_of(a.model, a.input, a.output, prices)
    event = {
        "ts": datetime.now(UTC).isoformat(timespec="seconds"),
        "project": a.project,
        "feature": a.feature or "_none",
        "task": a.task or "_none",
        "role": a.role,
        "model": a.model,
        "input_tokens": int(a.input),
        "output_tokens": int(a.output),
        "cache_read_tokens": int(a.cache_read or 0),
        "cost": cost,
        "note": a.note or "",
    }
    path = ledger_path(a.project)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(event, ensure_ascii=False) + "\n")
    print(json.dumps({"recorded": event, "ledger": str(path)}, ensure_ascii=False))
    return 0


def aggregate(events: list[dict], key: str | None) -> dict:
    total = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}
    buckets: dict[str, dict] = defaultdict(lambda: {"calls": 0, "in": 0, "out": 0, "cost": 0.0})
    for e in events:
        for tgt in (total, buckets[e.get(key) or "_none"]):
            tgt["calls"] += 1
            tgt["in"] += e["input_tokens"]
            tgt["out"] += e["output_tokens"]
            tgt["cost"] = round(tgt["cost"] + float(e["cost"]), 6)
    return {"total": total, "by": dict(sorted(buckets.items(), key=lambda kv: -kv[1]["cost"]))}


def fmt(block: dict) -> str:
    return f"{block['calls']:>3} вызовов  in {block['in']:>9}  out {block['out']:>7}  = {block['cost']:>10.4f}"


def cmd_report(a: argparse.Namespace) -> int:
    projects = [a.project] if a.project else [p.name for p in STATE.iterdir() if p.is_dir()] if STATE.exists() else []
    if a.all:
        projects = [p.name for p in STATE.iterdir() if p.is_dir()]
    if not projects:
        print("нет данных", file=sys.stderr)
        return 1

    grand = {"calls": 0, "in": 0, "out": 0, "cost": 0.0}
    data = {}
    for proj in projects:
        events = [
            e
            for e in read_events(proj)
            if (not a.feature or e.get("feature") == a.feature) and (not a.task or e.get("task") == a.task)
        ]
        if not events:
            continue
        data[proj] = {
            "total": aggregate(events, None)["total"],
            "by_feature": aggregate(events, "feature")["by"],
            "by_task": aggregate(events, "task")["by"],
            "by_model": aggregate(events, "model")["by"],
            "by_role": aggregate(events, "role")["by"],
        }
        for k in grand:
            grand[k] += data[proj]["total"][k]

    if a.json:
        print(json.dumps({"grand_total": grand, "projects": data}, ensure_ascii=False, indent=1))
        return 0

    print(f"ИТОГО по {', '.join(data)}: {fmt(grand)}")
    for proj, d in data.items():
        print(f"\n■ {proj}: {fmt(d['total'])}")
        for title, key in (("фичи", "by_feature"), ("задачи", "by_task"), ("модели", "by_model"), ("роли", "by_role")):
            print(f"  {title}:")
            for name, block in d[key].items():
                print(f"    {name:<28} {fmt(block)}")
    return 0


def cmd_price(a: argparse.Namespace) -> int:
    prices = load_prices(refresh=a.refresh, project=a.project)
    print(f"цен в кэше: {len(prices)} → {PRICES}")
    for model in list(prices)[:5]:
        p = prices[model]
        print(f"  {model:<40} in={p['in']:.3e} out={p['out']:.3e}")
    return 0


def cmd_projects(_: argparse.Namespace) -> int:
    if not STATE.exists():
        print("нет данных")
        return 0
    for p in sorted(STATE.iterdir()):
        if p.is_dir():
            n = len(read_events(p.name))
            print(f"{p.name:<24} событий: {n}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Учёт токенов и денег каскада")
    sp = ap.add_subparsers(dest="cmd", required=True)

    r = sp.add_parser("record")
    r.add_argument("-p", "--project", required=True)
    r.add_argument("-f", "--feature")
    r.add_argument("-t", "--task")
    r.add_argument("-r", "--role", required=True)
    r.add_argument("-m", "--model", required=True)
    r.add_argument("--input", type=int, required=True)
    r.add_argument("--output", type=int, required=True)
    r.add_argument("--cache-read", type=int, default=0)
    r.add_argument("--cost", type=float, default=None)
    r.add_argument("--note", default="")
    r.set_defaults(func=cmd_record)

    rep = sp.add_parser("report")
    rep.add_argument("-p", "--project")
    rep.add_argument("-f", "--feature")
    rep.add_argument("-t", "--task")
    rep.add_argument("--all", action="store_true")
    rep.add_argument("--json", action="store_true")
    rep.set_defaults(func=cmd_report)

    pr = sp.add_parser("price")
    pr.add_argument("--refresh", action="store_true")
    pr.add_argument("-p", "--project", help="project whose config gives the gateway and key")
    pr.set_defaults(func=cmd_price)

    pl = sp.add_parser("projects")
    pl.set_defaults(func=cmd_projects)

    a = ap.parse_args()
    return a.func(a)


if __name__ == "__main__":
    raise SystemExit(main())
