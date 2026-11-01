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

CLI: python3 validate_plan.py plan.json [--repo /path/to/repo]
Exit code 0 — план валиден, 1 — есть ошибки (печатаются в stderr).
"""

from __future__ import annotations

import json
import os
import sys

TESTS_PREFIXES = ("tests/", "test/")
SRC_PREFIXES = ("src/", "app/", "lib/", "pkg/")
CONTOUR_PREFIXES = ("backend/", "frontend/")  # монорепо: контур перед src/tests


def classify(path: str) -> str:
    """Отбрасывает префикс контура, чтобы src/ и tests/ распознавались в монорепо."""
    for prefix in CONTOUR_PREFIXES:
        if path.startswith(prefix):
            return path[len(prefix) :]
    return path


def load_schema() -> dict:
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "schema", "plan.schema.json")
    with open(p) as f:
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


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 2
    plan_path = args[0]
    repo = None
    if "--repo" in sys.argv:
        repo = sys.argv[sys.argv.index("--repo") + 1]

    with open(plan_path) as f:
        plan = json.load(f)

    errors = validate_schema(plan, load_schema())
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1

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

    by_id = {s["id"]: s for s in subs}
    for s in subs:
        files = s["files_allowed"]
        has_tests = any(classify(f).startswith(TESTS_PREFIXES) for f in files)
        has_src = any(classify(f).startswith(SRC_PREFIXES) for f in files)
        if has_tests and has_src:
            errors.append(
                f"{s['id']}: файлы тестов и исходников в одной подзадаче — "
                f"нарушает разделение «тесты пишет не тот, кто код»"
            )
        if not has_tests and not has_src and not files:
            errors.append(f"{s['id']}: files_allowed пуст")
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

    if not any(any(classify(f).startswith(SRC_PREFIXES) for f in s["files_allowed"]) for s in subs):
        errors.append("в плане нет ни одной подзадачи с исходниками — задачи без кода не считаются покрытыми")

    if errors:
        print("\n".join("ERROR: " + e for e in errors), file=sys.stderr)
        return 1
    print(
        f"OK: план '{plan['task']['id']}' валиден, подзадач: {len(subs)}, "
        f"файлов: {len({f for s in subs for f in s['files_allowed']})}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
