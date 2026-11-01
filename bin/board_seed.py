#!/usr/bin/env python3
"""board_seed.py — put a roadmap on the board as cards, idempotently.

Reads the roadmap produced by roadmap.py, creates the board project if it does not exist
(standard eight columns), one label per stage, one card per task plus the designer card.

CLI:
  board_seed.py --project <slug> --roadmap <roadmap.json> [--board-title TITLE]
                [--reset]           # --reset deletes the cards it would otherwise update

Output (stdout, one JSON object): {"project", "board", "created": [...], "updated": [...],
"skipped": [...], "url"}

State:
  ~/.local/state/cascade/<slug>/board.json — board project/view binding
  ~/.local/state/cascade/<slug>/cards.json — roadmap task id -> board task id

Exit codes: 0 — the board matches the roadmap, 1 — failure.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BOARD_LIB = Path.home() / "soft" / "vikunja"
sys.path.insert(0, str(BOARD_LIB))

from board import Board  # noqa: E402

STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cascade"
DEFAULT_TITLE = "OCCC — OpenCapsuleerCommandCenter"
STAGE_COLORS = ["2e7d9a", "2e9a6b", "5b8f3a", "9a8f2e", "9a6b2e", "9a3a4f", "7a3a9a", "3a5f9a", "6b6b6b", "4a4a4a"]


def load_roadmap(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("roadmap", data)


def card_body(stage: dict, task: dict) -> str:
    def bullets(items) -> str:
        return "\n".join(f"- {item}" for item in items or []) or "—"

    depends = ", ".join(task.get("depends_on") or []) or "—"
    esi = task.get("esi") or {}
    endpoints = ", ".join(esi.get("endpoints") or []) or "—"
    scopes = ", ".join(esi.get("scopes") or []) or "—"
    risks = bullets(task.get("risks"))
    why_first = (task.get("why_first") or "").strip()

    parts = [
        f"**Этап:** {stage.get('id', '').upper()} — {stage.get('title', '')}"
        f" · сложность этапа {stage.get('complexity', '?')}/5",
        f"**Размер:** {task.get('size', '?')} · **Сложность:** {task.get('difficulty', '?')}/5"
        f" · **Роли:** {', '.join(task.get('roles') or [])}",
        "",
        "**Зачем**",
        task.get("goal", ""),
        "",
        "**В объёме**",
        bullets(task.get("scope")),
        "",
        "**Вне объёма**",
        bullets(task.get("out_of_scope")),
        "",
        "**Приёмка**",
        "\n".join(f"- [ ] {item}" for item in task.get("acceptance") or []) or "—",
        "",
        f"**Зависит от:** {depends}",
        f"**ESI:** {endpoints}",
        f"**Скоупы:** {scopes}",
    ]
    if risks != "—":
        parts += ["**Риски**", risks]
    if why_first:
        parts += ["**Почему первой (отладка конвейера)**", why_first]
    parts += [
        "",
        f"<sub>roadmap id: {task.get('id', '')} · модель-архитектор: см. карточку этапа</sub>",
    ]
    return "\n".join(parts)


def stage_body(stage: dict) -> str:
    return "\n".join(
        [
            f"**Этап {stage.get('id', '').upper()} — {stage.get('title', '')}**",
            f"Сложность: {stage.get('complexity', '?')}/5",
            "",
            stage.get("intent", ""),
            "",
            "**Почему на этом месте**",
            stage.get("why_here", ""),
            "",
            "**Критерии завершения этапа**",
            "\n".join(f"- [ ] {item}" for item in stage.get("exit_criteria") or []) or "—",
            "",
            "**Задачи этапа**",
            "\n".join(f"- {task.get('id')} — {task.get('title')}" for task in stage.get("tasks") or []),
        ]
    )


def designer_body(brief: dict) -> str:
    return "\n".join(
        [
            "**Отдельная карточка для роли `designer`.** Дизайнер придумывает интерфейс, но не пишет код.",
            "Начинать до первой UI-задачи: без токенов, сетки и состояний исполнитель начнёт сочинять дизайн сам.",
            "",
            f"**Задача:** {brief.get('title', 'Дизайн-система OCCC')}",
            "",
            brief.get("goal", ""),
            "",
            "**Что должно быть решено**",
            "\n".join(f"- [ ] {item}" for item in brief.get("must_cover") or []) or "—",
            "",
            "**Вход**",
            "- ADR 0008 (дизайн-токены и двуязычный каталог), `frontend/src/theme/tokens.ts`",
            "- Скриншоты текущей оболочки, список страниц из роадмапа",
            "",
            "**Выход**",
            "- design spec: сетка, состояния, состав компонентов, дельта токенов",
            "- макеты ключевых экранов и потоков (регистрация/скоупы, кабинет, склады, производство, рынок)",
            "",
            "**Приёмка**",
            "- [ ] У каждого экрана из роадмапа есть макет и описанные состояния (пусто/загрузка/ошибка/нет доступа)",
            "- [ ] Правки токенов оформлены как минимальная дельта к `tokens.ts`, а не «переделать всё»",
            "- [ ] Компоненты названы и сопоставлены с Ant Design 6 (что берём готовым, что оборачиваем)",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the board from a roadmap.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--roadmap", required=True)
    parser.add_argument("--board-title", default=DEFAULT_TITLE)
    parser.add_argument("--reset", action="store_true", help="delete previously seeded cards before writing")
    parser.add_argument("--markdown", help="also write a readable markdown digest here")
    args = parser.parse_args()

    roadmap = load_roadmap(Path(os.path.expanduser(args.roadmap)))
    stages = roadmap.get("stages") or []
    if not stages:
        print("ERROR: the roadmap has no stages", file=sys.stderr)
        return 1

    state_dir = STATE / args.project
    state_dir.mkdir(parents=True, exist_ok=True)
    binding_path = state_dir / "board.json"
    binding = json.loads(binding_path.read_text()) if binding_path.exists() else {}

    root = Board()
    project_id = binding.get("project_id")
    existing_projects = {p["id"]: p for p in root.projects()}
    if not project_id or project_id not in existing_projects:
        match = root.project_by_title(args.board_title)
        if match:
            project_id = match["id"]
        else:
            project_id = root.create_project(
                args.board_title,
                "План развития OpenCapsuleerCommandCenter (OCCC): этапы от отладки конвейера "
                "до производства и рынка. Источник — roadmap.py, карточки идемпотентны.\n\n"
                + str(roadmap.get("product_summary", "")),
            )
    views = root._call("GET", f"/projects/{project_id}/views")[1]
    kanban = next((v for v in views or [] if v.get("view_kind") == "kanban"), None)
    if not kanban:
        print("ERROR: the board project has no kanban view", file=sys.stderr)
        return 1
    board = Board(project_id=project_id, view_id=kanban["id"])
    binding = {"project_id": project_id, "view_id": kanban["id"], "title": args.board_title, "base": board.base}
    binding_path.write_text(json.dumps(binding, ensure_ascii=False, indent=1) + "\n")

    labels = board.labels()
    label_ids: dict[str, int] = {}
    for index, stage in enumerate(stages):
        name = f"{args.project.upper()} · {stage['id'].upper()} {stage.get('title', '')}"[:80]
        label_id = labels.get(name)
        if not label_id:
            label_id = board.create_label(name, STAGE_COLORS[index % len(STAGE_COLORS)], stage.get("intent", "")[:250])
        label_ids[stage["id"]] = label_id
    epic_label = labels.get("epic")
    if not epic_label:
        # Контейнерная карточка (этап): sync её не двигает, иначе все этапы уезжают в Plan.
        epic_label = board.create_label("epic", "8e44ad",
                                        "Контейнерная карточка (этап, бриф), а не работа")
    designer_label = labels.get(f"{args.project.upper()} · designer")
    if not designer_label:
        designer_label = board.create_label(f"{args.project.upper()} · designer", "d94f8a", "Задачи роли designer")

    cards_path = state_dir / "cards.json"
    cards = json.loads(cards_path.read_text()) if cards_path.exists() else {}
    if args.reset:
        titles = {f"{t['id']} — {t['title']}" for s in stages for t in s.get("tasks") or []}
        for task in board.tasks():
            if task["title"] in titles or task["title"].startswith("D0 —"):
                board.delete(task["id"])
        cards = {}

    existing = {t["title"]: t["id"] for t in board.tasks()}
    created, updated, skipped = [], [], []

    def ensure(title: str, body: str, labels_wanted: list[int]) -> int:
        task_id = existing.get(title)
        if task_id:
            current = board.get(task_id)
            if (current.get("description") or "").strip() != body.strip():
                board.update(task_id, description=body)
                updated.append(title)
            else:
                skipped.append(title)
        else:
            task_id = board.create(title, body, "Backlog", labels=labels_wanted)
            existing[title] = task_id
            created.append(title)
        return task_id

    for stage in stages:
        stage_card = f"Этап {stage['id'].upper()} — {stage.get('title', '')}"
        ensure(stage_card, stage_body(stage), [label_ids[stage["id"]], epic_label])
        for task in stage.get("tasks") or []:
            title = f"{task['id']} — {task.get('title', '')}"[:250]
            task_id = ensure(title, card_body(stage, task), [label_ids[stage["id"]]])
            cards[task["id"]] = task_id

    brief = roadmap.get("designer_brief") or {}
    if brief:
        ensure(
            f"D0 — {brief.get('title', 'Дизайн-система и бриф дизайнера')}"[:250],
            designer_body(brief),
            [designer_label],
        )

    cards_path.write_text(json.dumps(cards, ensure_ascii=False, indent=1) + "\n")

    if args.markdown:
        lines = [
            "# План развития OCCC",
            "",
            str(roadmap.get("product_summary", "")).strip(),
            "",
            "## Ограничения",
            "",
        ]
        lines += [f"- {item}" for item in roadmap.get("constraints") or []]
        for stage in stages:
            lines += [
                "",
                f"## {stage['id'].upper()} — {stage.get('title', '')}"
                f"  ·  сложность {stage.get('complexity', '?')}/5"
                + ("  ·  необязательный" if stage.get("optional") else ""),
                "",
                str(stage.get("intent", "")).strip(),
                "",
            ]
            for task in stage.get("tasks") or []:
                lines += [
                    f"### {task['id']} — {task.get('title', '')}",
                    f"`{task.get('size')}` · сложность {task.get('difficulty')}/5 · "
                    f"роли: {', '.join(task.get('roles') or [])}",
                    "",
                    str(task.get("goal", "")).strip(),
                    "",
                ]
                if task.get("scope"):
                    lines += ["В объёме:", *[f"- {i}" for i in task["scope"]], ""]
                if task.get("out_of_scope"):
                    lines += ["Вне объёма:", *[f"- {i}" for i in task["out_of_scope"]], ""]
                if task.get("acceptance"):
                    lines += ["Приёмка:", *[f"- [ ] {i}" for i in task["acceptance"]], ""]
                if task.get("depends_on"):
                    lines += [f"Зависит от: {', '.join(task['depends_on'])}", ""]
        brief = roadmap.get("designer_brief") or {}
        if brief:
            lines += ["", f"## D0 — {brief.get('title', 'Бриф дизайнера')}", "", str(brief.get("goal", "")).strip(), ""]
            lines += [f"- [ ] {item}" for item in brief.get("must_cover") or []]
        Path(os.path.expanduser(args.markdown)).write_text("\n".join(lines) + "\n", encoding="utf-8")

    result = {
        "project": args.project,
        "board": {
            "title": args.board_title,
            "project_id": project_id,
            "view_id": kanban["id"],
            "url": board.base.replace("/api/v1", ""),
        },
        "created": created,
        "updated": updated,
        "skipped": skipped,
        "cards": len(cards),
        "stages": len(stages),
    }
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
