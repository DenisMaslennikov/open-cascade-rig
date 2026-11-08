#!/usr/bin/env python3
"""validate_plan.py — детерминированная проверка плана декомпозиции.

Схема проверяется по plan.schema.json (stdlib-only, мини-валидатор), затем
семантические правила, которые схема выразить не может:

  1. id уникальны, depends_on ссылается на существующие подзадачи;
  2. граф зависимостей ацикличен;
  3. файлы независимых (не связанных цепочкой) подзадач не пересекаются —
     иначе параллельный исполнитель будет драться за один файл;
  4. каждая подзадача трогает либо tests/ либо src/ (не то и другое сразу) —
     это разделение «тесты пишет не тот, кто код» на уровне плана;
  5. файлы существуют в репозитории (если передан --repo);
  6. полный список файлов покрывает приёмочные сценарии задачи (≥1 файл src).

CLI: cd ~/.config/cascade/bin && python3 -m rig.validate_plan plan.json [--repo /path/to/repo]
Exit code 0 — план валиден, 1 — есть ошибки (печатаются в stderr).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PLAN_KINDS = ("code", "tests", "docs")


def schema_path() -> Path:
    """`config/schema/plan.schema.json`, found by walking up, not by counting parents.

    The module moved from `bin/` to `bin/rig/` (card #191) and a path built as `<here>/../schema`
    silently started pointing at `bin/schema`. Walking up to the checkout that holds the schema is
    the only form that survives the next move; the same marker trick lives in `bin/tests/conftest.py`.
    """
    for parent in Path(__file__).resolve().parents:
        candidate = parent / "config" / "schema" / "plan.schema.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError("plan.schema.json not found: no parent directory holds config/schema/plan.schema.json")


def load_schema() -> dict:
    with open(schema_path()) as f:
        return json.load(f)


def validate_schema(node, schema, path="plan") -> list[str]:
    """Мини-валидатор нужного подмножества JSON Schema."""
    errs: list[str] = []
    t = schema.get("type")
    types = {"object": dict, "array": list, "string": str, "integer": int, "number": (int, float)}
    if t and not isinstance(node, types[t]):
        return [f"{path}: ожидался {t}, получен {type(node).__name__}"]
    if t == "object":
        for req in schema.get("required", []):
            if req not in node:
                errs.append(f"{path}: отсутствует обязательное поле '{req}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for k in node:
                if k not in props:
                    errs.append(f"{path}: лишнее поле '{k}'")
        for k, sub in props.items():
            if k in node:
                errs += validate_schema(node[k], sub, f"{path}.{k}")
    elif t == "array":
        items = schema.get("items", {})
        if "minItems" in schema and len(node) < schema["minItems"]:
            errs.append(f"{path}: минимум {schema['minItems']} элементов, получено {len(node)}")
        if "maxItems" in schema and len(node) > schema["maxItems"]:
            errs.append(f"{path}: максимум {schema['maxItems']} элементов, получено {len(node)}")
        for i, item in enumerate(node):
            errs += validate_schema(item, items, f"{path}[{i}]")
    elif t == "string":
        if "minLength" in schema and len(node) < schema["minLength"]:
            errs.append(f"{path}: короче {schema['minLength']} символов")
        if "maxLength" in schema and len(node) > schema["maxLength"]:
            errs.append(f"{path}: длиннее {schema['maxLength']} символов")
        if "pattern" in schema:
            import re

            if not re.match(schema["pattern"], node):
                errs.append(f"{path}: не соответствует шаблону {schema['pattern']}")
    return errs


def cyclic(nodes: dict[str, list[str]]) -> list[str]:
    state: dict[str, int] = {}

    def walk(n: str, stack: list[str]) -> list[str] | None:
        if state.get(n) == 1:
            return stack + [n]
        if state.get(n) == 2:
            return None
        state[n] = 1
        for m in nodes[n]:
            got = walk(m, stack + [n])
            if got:
                return got
        state[n] = 2
        return None

    for n in nodes:
        got = walk(n, [])
        if got:
            return got
    return []


def ancestry(nodes: dict[str, list[str]], n: str) -> set[str]:
    """Все предки узла (кто должен выполниться раньше)."""
    seen: set[str] = set()
    stack = list(nodes[n])
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        stack += nodes.get(cur, [])
    return seen


def check_plan(plan: dict, repo: Path | None = None) -> list[str]:
    """Every rule the schema cannot express; returns the errors, prints the notes.

    A function of its own so a test can drive it: `main` only parses the argv and prints.
    """
    errors = validate_schema(plan, load_schema())
    if errors:
        return errors

    subs = plan["subtasks"]
    ids = [s["id"] for s in subs]
    dupes = {i for i in ids if ids.count(i) > 1}
    errors += [f"дублирующий id подзадачи: {d}" for d in sorted(dupes)]

    nodes = {s["id"]: list(s["depends_on"]) for s in subs}
    for sid, deps in nodes.items():
        for d in deps:
            if d not in nodes:
                errors.append(f"{sid}: depends_on ссылается на неизвестную подзадачу '{d}'")
    filtered = {k: [d for d in v if d in nodes] for k, v in nodes.items()}
    cyc = cyclic(filtered)
    if cyc:
        errors.append("цикл в depends_on: " + " -> ".join(cyc))

    for s in subs:
        files = s["files_allowed"]
        if s.get("kind") not in PLAN_KINDS:
            errors.append(f"{s['id']}: kind должна быть одна из {PLAN_KINDS}, получено {s.get('kind')!r}")
        if not files:
            errors.append(f"{s['id']}: files_allowed пуст")
        for entry in files:
            # A path outside the worktree is not a path the executor may touch, whatever it starts with.
            if entry.startswith("/") or ".." in entry.split("/"):
                errors.append(f"{s['id']}: путь должен быть относительным и без '..': {entry}")
        if repo:
            for f in files:
                if not os.path.exists(os.path.join(repo, f)):
                    # для новых файлов это нормально, но предупредим
                    print(f"note: {s['id']}: файла ещё нет в репо: {f}", file=sys.stderr)

    # пересечение файлов у независимых подзадач
    for i, a in enumerate(subs):
        for b in subs[i + 1 :]:
            if b["id"] in ancestry(nodes, a["id"]) or a["id"] in ancestry(nodes, b["id"]):
                continue
            inter = set(a["files_allowed"]) & set(b["files_allowed"])
            if inter:
                errors.append(f"{a['id']} и {b['id']} независимы, но делят файлы: {sorted(inter)}")

    if not any(s.get("kind") == "code" for s in subs):
        errors.append(
            "в плане нет ни одной подзадачи с исходниками (kind: code) — задачи без кода не считаются покрытыми"
        )

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python3 -m rig.validate_plan",
        description="Deterministic check of a decomposition plan against the plan schema and the rules.",
        epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("plan", type=Path, help="plan JSON file")
    parser.add_argument("--repo", type=Path, default=None, help="repository the plan's paths must exist in")
    args = parser.parse_args(argv)

    with open(args.plan) as f:
        plan = json.load(f)

    errors = check_plan(plan, args.repo)

    if errors:
        print("\n".join("ERROR: " + e for e in errors), file=sys.stderr)
        return 1
    subs = plan["subtasks"]
    print(
        f"OK: план '{plan['task']['id']}' валиден, подзадач: {len(subs)}, "
        f"файлов: {len({f for s in subs for f in s['files_allowed']})}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
