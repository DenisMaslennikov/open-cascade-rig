#!/usr/bin/env python3
"""board_archive.py — прибрать доску: завершённые карточки в архив, карточки без колонки — в колонку.

Зачем: карточка, чья работа влита в main, но которая стоит в Backlog/Review, делает доску
лживой — по ней нельзя понять, что реально осталось. «Архив» Vikunja — это `done=true`
(kanban-вид скрывает done-карточки, пока не включён показ выполненных), плюс перенос в
колонку Done, чтобы было одно место, где смотреть закрытое.

Отдельный случай — карточки без bucket: `board.py create` переносит карточку в колонку,
но карточки, созданные в обход него (и карточки после пересоздания вида), остаются без
bucket и **не видны на доске вообще**. `--orphans` возвращает их в колонку.

Usage:
  board_archive.py --project occc --status
  board_archive.py --project occc --cards 15,16 [--note "PR #3 merged"] [--dry-run]
  board_archive.py --project occc --orphans [--to Backlog] [--dry-run]

Output: JSON в stdout, ошибки в stderr. Exit 0 — ок, 1 — сбой.
"""
from __future__ import annotations

import argparse
import json
import os
import sys

BOARD_DIR = os.path.expanduser("~/soft/vikunja")
STATE_DIR = os.path.expanduser("~/.local/state/cascade")
sys.path.insert(0, BOARD_DIR)

import board as board_mod  # noqa: E402


def open_board(slug: str):
    path = os.path.join(STATE_DIR, slug, "board.json")
    if not os.path.exists(path):
        raise SystemExit(f"нет привязки доски: {path}")
    with open(path) as f:
        cfg = json.load(f)
    return board_mod.Board(project_id=cfg["project_id"], view_id=cfg["view_id"])


def all_tasks(b, page_size: int = 50) -> list[dict]:
    out: list[dict] = []
    for page in range(1, 20):
        _, chunk = b._call("GET", f"/projects/{b.project_id}/tasks?per_page={page_size}&page={page}")
        if not chunk:
            break
        out.extend(chunk)
        if len(chunk) < page_size:
            break
    return out


def view_map(b) -> dict[int, tuple[str, dict]]:
    """task_id -> (column title, task payload) for cards that are actually on the board.

    The kanban view endpoint caps a bucket at 50 tasks per page and hands back the *next*
    slice of the same bucket on `page=2`, so a single request silently hides the rest and
    every hidden card looks like an orphan. Walk pages until one comes back empty.
    """
    out: dict[int, tuple[str, dict]] = {}
    for page in range(1, 20):
        _, buckets = b._call(
            "GET", f"/projects/{b.project_id}/views/{b.view_id}/tasks?per_page=200&page={page}")
        fresh = 0
        for bucket in buckets:
            for task in bucket.get("tasks") or []:
                if task["id"] not in out:
                    fresh += 1
                out[task["id"]] = (bucket["title"], task)
        if not fresh:
            break
    return out


def archive(b, task: dict, column: str, note: str | None, dry: bool) -> dict:
    """Move one card to Done and mark it done. Idempotent: an archived card is skipped."""
    act: dict = {"id": task["id"], "title": task["title"][:80], "before": column, "actions": []}
    if column != "Done":
        act["actions"].append(f"move:{column}->Done")
        if not dry:
            b.move(task["id"], "Done")
    if not task.get("done"):
        act["actions"].append("done:true")
        if not dry:
            b.update(task["id"], done=True)
    if note:
        act["actions"].append("comment")
        if not dry:
            b.comment(task["id"], note)
    act["changed"] = bool(act["actions"])
    return act


def main() -> int:
    ap = argparse.ArgumentParser(description="Прибрать доску: архив завершённых и возврат «осиротевших» карточек")
    ap.add_argument("--project", required=True, help="slug проекта (берёт state/cascade/<slug>/board.json)")
    ap.add_argument("--cards", help="id карточек через запятую — перенести в Done и закрыть")
    ap.add_argument("--orphans", action="store_true", help="карточки без колонки вернуть в --to")
    ap.add_argument("--to", default="Backlog", help="колонка для --orphans (по умолчанию Backlog)")
    ap.add_argument("--note", help="комментарий, который дописывается к каждой архивной карточке")
    ap.add_argument("--status", action="store_true", help="только показать состояние доски")
    ap.add_argument("--dry-run", action="store_true", help="ничего не менять, показать план")
    args = ap.parse_args()

    b = open_board(args.project)
    columns = b.columns()
    tasks = {t["id"]: t for t in all_tasks(b)}
    on_board = view_map(b)
    orphans = sorted(tid for tid in tasks if tid not in on_board)

    if args.status or (not args.cards and not args.orphans):
        per_column: dict[str, int] = {t: 0 for t in columns}
        for column, _task in on_board.values():
            per_column[column] = per_column.get(column, 0) + 1
        print(json.dumps({
            "project": args.project,
            "columns": per_column,
            "cards_total": len(tasks),
            "on_board": len(on_board),
            "orphans": len(orphans),
            "orphan_ids": orphans[:20],
            "done": sum(1 for t in tasks.values() if t.get("done")),
        }, ensure_ascii=False))
        return 0

    report: dict = {"project": args.project, "dry_run": args.dry_run, "archived": [], "moved": []}

    if args.cards:
        requested = [int(x) for x in args.cards.replace(" ", "").split(",") if x]
        missing = [tid for tid in requested if tid not in tasks]
        if missing:
            print(f"нет таких карточек: {missing}", file=sys.stderr)
            return 1
        for tid in requested:
            task = tasks[tid]
            column = on_board.get(tid, ("<none>", task))[0]
            report["archived"].append(archive(b, task, column, args.note, args.dry_run))

    if args.orphans:
        if args.to not in columns:
            print(f"нет колонки {args.to!r}; есть {list(columns)}", file=sys.stderr)
            return 1
        for tid in orphans:
            task = tasks[tid]
            note = "orphan->column"
            if not args.dry_run:
                b.move(tid, args.to)
            report["moved"].append({"id": tid, "title": task["title"][:80], "to": args.to, "why": note})

    if not args.dry_run and (report["archived"] or report["moved"]):
        # Re-read: a successful call is not a successful task.
        after = view_map(b)
        fresh = {t["id"]: t for t in all_tasks(b)}
        report["verify"] = {
            "done_on_board": sum(1 for tid, (col, t) in after.items() if t.get("done") and col != "Done"),
            "archived_in_done": sum(1 for tid, (col, _t) in after.items()
                                    if col == "Done" and fresh.get(tid, {}).get("done")),
            "orphans_left": sorted(tid for tid in fresh if tid not in after),
        }
    print(json.dumps(report, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
