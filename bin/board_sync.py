#!/usr/bin/env python3
"""board_sync.py — держать доску в согласии с GitHub: PR → Review, мерж → архив, разблокировка → Plan.

Зачем: доска — инструмент статуса, а статус меняет не человек, а события GitHub (PR открыт, PR влит).
Если карточку двигать руками «после мержа», она рано или поздно останется в Review, а карточки,
которые перестали быть заблокированными, будут лежать в Backlog и никто их не возьмёт.

Правила, которые скрипт приводит в исполнение:

1. Открытый PR с `#<id>` карточки в заголовке → карточка в колонке `Review`.
2. Влитый PR → карточка заархивирована (`done` + `Done`).
3. Карточка, у которой все блокирующие карточки (`blocking`/`blocked` relation) закрыты, выходит
   из `Backlog` в `Plan` — ровно один раз, дальше её двигает работа, а не скрипт.
4. Пока блокирующий PR не влит, карточка **не берётся в работу**: `--gate <id>` возвращает 1,
   если у карточки есть незакрытый блокер. Это проверка инструментом, а не пожелание в тексте.

CLI:
  board_sync.py --project occc [--repo PATH] [--apply] [--dry-run]
  board_sync.py --project occc --gate 17

Output: JSON. Exit 0 — ок (для --gate: 0 — карточку брать можно), 1 — сбой или заблокирована.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

BOARD_DIR = os.path.expanduser("~/soft/vikunja")
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cascade"
sys.path.insert(0, BOARD_DIR)

import board as board_mod  # noqa: E402

DONE_COLUMN = "Done"
ACTIVE_COLUMNS = ("Plan", "Tests", "Code", "Hardening", "Audit", "Review")
PR_REF = re.compile(r"#(\d+)")


def open_board(slug: str):
    path = STATE / slug / "board.json"
    if not path.exists():
        raise SystemExit(f"нет привязки доски: {path}")
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return board_mod.Board(project_id=cfg["project_id"], view_id=cfg["view_id"])


def all_cards(b, page_size: int = 50) -> list[dict]:
    out: list[dict] = []
    for page in range(1, 20):
        _, chunk = b._call("GET", f"/projects/{b.project_id}/tasks?per_page={page_size}&page={page}")
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page_size:
            break
    return out


def column_map(b) -> dict[int, str]:
    """task_id -> column title, walking pages: the view endpoint caps a column at 50 per page."""
    out: dict[int, str] = {}
    for page in range(1, 20):
        _, buckets = b._call(
            "GET", f"/projects/{b.project_id}/views/{b.view_id}/tasks?per_page=200&page={page}")
        fresh = 0
        for bucket in buckets:
            for task in bucket.get("tasks") or []:
                if task["id"] not in out:
                    fresh += 1
                out[task["id"]] = bucket["title"]
        if not fresh:
            break
    return out


def gh_prs(repo: Path) -> list[dict]:
    proc = subprocess.run(
        ["gh", "pr", "list", "--state", "all", "--limit", "200",
         "--json", "number,title,state,headRefName,mergedAt"],
        cwd=repo, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"gh pr list failed: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "[]")


def pr_cards(prs: list[dict], cards: dict[int, dict]) -> dict[int, dict]:
    """card id -> newest PR that mentions it, matched by `#<id>` in the title."""
    found: dict[int, dict] = {}
    for pr in prs:
        for num in PR_REF.findall(pr.get("title") or ""):
            cid = int(num)
            if cid not in cards:
                continue
            current = found.get(cid)
            if current is None or pr["number"] > current["number"]:
                found[cid] = pr
    return found


def blockers_of(card: dict) -> list[int]:
    """Cards that must be done before this one.

    Vikunja mirrors a relation on both sides: `blocking` lists the tasks *this* card blocks,
    `blocked` lists the tasks blocking it. Reading both turns every blocker into its own dependent,
    and a card looks blocked by the work that waits for it.
    """
    related = card.get("related_tasks") or {}
    ids: list[int] = []
    for item in related.get("blocked") or []:
        if isinstance(item, dict) and item.get("id"):
            ids.append(int(item["id"]))
    return ids


def main() -> int:
    ap = argparse.ArgumentParser(description="Sync the board with GitHub and move unblocked cards to Plan")
    ap.add_argument("--project", required=True)
    ap.add_argument("--repo", help="repository path; defaults to ~/projects/<slug>")
    ap.add_argument("--gate", type=int, help="exit 1 when this card is still blocked")
    ap.add_argument("--apply", action="store_true", help="write changes (default is a dry run)")
    ap.add_argument("--no-github", action="store_true", help="skip the PR pass, only unblock")
    args = ap.parse_args()

    b = open_board(args.project)
    repo = Path(os.path.expanduser(args.repo or f"~/projects/{args.project}"))
    cards = {c["id"]: c for c in all_cards(b)}
    columns = column_map(b)

    if args.gate:
        card = cards.get(args.gate)
        if card is None:
            print(json.dumps({"ok": False, "error": f"нет карточки {args.gate}"}, ensure_ascii=False))
            return 1
        open_blockers = [i for i in blockers_of(card)
                         if i in cards and not cards[i].get("done")]
        report = {"card": args.gate, "title": card["title"][:80],
                  "column": columns.get(args.gate, "<none>"),
                  "blocked_by": [{"id": i, "title": cards[i]["title"][:60],
                                  "column": columns.get(i, "<none>")} for i in open_blockers],
                  "takeable": not open_blockers}
        print(json.dumps(report, ensure_ascii=False, indent=1))
        return 1 if open_blockers else 0

    actions: list[dict] = []
    prs = [] if args.no_github else gh_prs(repo)
    linked = pr_cards(prs, cards)

    for cid, pr in sorted(linked.items()):
        card = cards[cid]
        column = columns.get(cid, "<none>")
        if pr["state"] == "OPEN" and not card.get("done"):
            if column != "Review":
                actions.append({"card": cid, "action": f"move:{column}->Review", "pr": pr["number"]})
                if args.apply:
                    b.move(cid, "Review")
        elif pr.get("mergedAt") and not card.get("done"):
            actions.append({"card": cid, "action": "archive", "pr": pr["number"]})
            if args.apply:
                if column != DONE_COLUMN:
                    b.move(cid, DONE_COLUMN)
                b.update(cid, done=True)

    # An unblocked card leaves Backlog for Plan exactly once: after that it belongs to the work.
    if args.apply:
        cards = {c["id"]: c for c in all_cards(b)}
        columns = column_map(b)
    for cid, card in sorted(cards.items()):
        if card.get("done") or columns.get(cid) != "Backlog":
            continue
        blockers = blockers_of(card)
        if blockers and all(cards.get(i, {}).get("done") for i in blockers):
            actions.append({"card": cid, "action": "move:Backlog->Plan", "unblocked_by": blockers})
            if args.apply:
                b.move(cid, "Plan")

    report = {"project": args.project, "applied": args.apply, "actions": actions,
              "open_prs": len([p for p in prs if p["state"] == "OPEN"]),
              "linked_cards": len(linked)}
    if args.apply and actions:
        after = column_map(b)
        report["verify"] = {"columns": {c: list(after.values()).count(c)
                                        for c in set(after.values())}}
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
