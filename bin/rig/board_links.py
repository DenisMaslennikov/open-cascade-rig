#!/usr/bin/env python3
"""board_links.py — apply a relation map to the board, idempotently.

Links live in one JSON file so the graph between cards is reproducible: schema cards block the
feature cards that need their tables, preparation tasks block the work that depends on them.

CLI:
  board_links.py --project <slug> --links links.json [--dry-run]

links.json:
  {"links": [{"from": 144, "to": 15, "kind": "blocking"}, ...]}

Output (stdout, one JSON object): {"created": n, "skipped": n, "errors": [...]}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path.home() / "soft" / "vikunja"))

from board import Board

STATE = Path.home() / ".local/state/cascade"


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply a relation map to the board.")
    parser.add_argument("--project", required=True, help="cascade project slug")
    parser.add_argument("--links", required=True)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    binding = json.loads((STATE / args.project / "board.json").read_text())
    board = Board(project_id=binding["project_id"], view_id=binding["view_id"])
    links = json.loads(Path(args.links).read_text(encoding="utf-8")).get("links") or []

    existing: dict[int, set[tuple[str, int]]] = {}

    def have(task_id: int) -> set[tuple[str, int]]:
        if task_id not in existing:
            rels = board.relations(task_id)
            existing[task_id] = {(kind, item["id"]) for kind, items in rels.items() for item in items or []}
        return existing[task_id]

    created = skipped = 0
    errors: list[str] = []
    for link in links:
        source, target, kind = int(link["from"]), int(link["to"]), link.get("kind", "related")
        if (kind, target) in have(source):
            skipped += 1
            continue
        if args.dry_run:
            created += 1
            continue
        try:
            board.relate(source, target, kind)
            have(source).add((kind, target))
            created += 1
        except Exception as exc:
            errors.append(f"{source} -{kind}-> {target}: {exc}")
    print(json.dumps({"created": created, "skipped": skipped, "errors": errors}, ensure_ascii=False, indent=1))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
