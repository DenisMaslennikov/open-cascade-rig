#!/usr/bin/env python3
"""check_project.py <slug> — проверяет, что проект сконфигурирован.

Никаких моделей по умолчанию: если у роли пустая модель, проект не готов
к запуску каскада. Читатель YAML — минимальный (плоские пары и один уровень
вложенности), чтобы не тащить PyYAML.

Exit 0 — всё задано; 1 — чего-то не хватает (список в stderr).
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

ROLES = ("architect", "chief_architect", "specifier", "executor", "arbiter", "reviewer", "vision")
UI_ROLES = ("designer",)  # required when the project declares `ui: true`
BLACKLIST = Path(__file__).resolve().parents[2] / "config" / "models-blacklist.yaml"


def _clean(raw: str) -> str:
    """Убирает inline-комментарий и кавычки: `"x"  # note` -> `x`."""
    val = re.sub(r"\s+#.*$", "", raw).strip()
    return val.strip('"').strip("'")


def parse_flat(path: str) -> dict[str, dict[str, str]]:
    """Читает `key: value` и один уровень вложенности `key:` + отступ 2."""
    out: dict[str, dict[str, str]] = {}
    section = "_"
    out[section] = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^  (\w+):\s*(.*)$", line)  # вложенный ключ
        if m:
            out.setdefault(section, {})[m.group(1)] = _clean(m.group(2))
            continue
        m = re.match(r"^(\w+):\s*(.*)$", line)  # верхний ключ
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val:
                out[section][key] = _clean(val)
            else:
                section = key
                out.setdefault(section, {})
    return out


def read_blacklist(path: Path) -> list[dict[str, str]]:
    """Reads the `models:` list of the blacklist file into one mapping per entry.

    The file is a flat list of `- model:` blocks with an indented `reason:`; same minimal reader
    philosophy as `parse_flat` — no PyYAML. Raises OSError when the file cannot be read.
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            line = raw.strip()
            if not line or line.startswith("#") or line == "models:":
                continue
            if line.startswith("- "):
                current = {}
                entries.append(current)
                line = line[2:].strip()
            if current is None:
                continue
            key, separator, value = line.partition(":")
            if separator:
                current[key.strip()] = _clean(value.strip())
    return entries


def blacklist_problems(models: dict[str, str], entries: list[dict[str, str]]) -> list[tuple[str, str, str]]:
    """One `(role, model, reason)` tuple per configured model an entry forbids, in role-name order.

    An entry matches by exact name or, when it ends with `*`, by prefix; both case-insensitively
    over the whole name. The reason is the matching entry's own, even when blank. An empty model is
    the unset failure of the caller, not a hit.
    """
    problems: list[tuple[str, str, str]] = []
    for role in sorted(models):
        model = models.get(role) or ""
        if not model:
            continue
        for entry in entries:
            pattern = (entry.get("model") or "").strip()
            if not pattern:
                continue
            prefix = pattern[:-1]
            matched = (
                model.lower().startswith(prefix.lower()) if pattern.endswith("*") else model.lower() == pattern.lower()
            )
            if matched:
                problems.append((role, model, entry.get("reason", "")))
    return problems


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    slug = sys.argv[1]
    path = os.path.expanduser(f"~/.config/cascade/projects/{slug}.yaml")
    if not os.path.exists(path):
        print(f"ERROR: нет конфига проекта {path}", file=sys.stderr)
        return 1

    cfg = parse_flat(path)
    models = cfg.get("models", {})
    has_ui = cfg.get("_", {}).get("ui", "").lower() in ("true", "yes", "1")
    required = ROLES + (UI_ROLES if has_ui else ())
    missing = [r for r in required if not models.get(r)]
    if missing:
        print(f"ERROR: {slug}: не заданы модели ролей: {', '.join(missing)}", file=sys.stderr)
        print(f"       заполните ~/.config/cascade/projects/{slug}.yaml → models:", file=sys.stderr)
        return 1

    try:
        entries = read_blacklist(BLACKLIST)
    except OSError:
        print(f"WARN: {slug}: список запрещённых моделей недоступен: {BLACKLIST}", file=sys.stderr)
        entries = []
    forbidden = blacklist_problems(models, entries)
    if forbidden:
        print(f"ERROR: {slug}: роли сконфигурированы моделями из запрещённого списка ({BLACKLIST}):", file=sys.stderr)
        for role, model, reason in forbidden:
            suffix = f" — {reason}" if reason else ""
            print(f"       {role}={model}{suffix}", file=sys.stderr)
        return 1

    key_ref = cfg.get("_", {}).get("key_ref") or "LITELLM_KEY"
    key_env = os.path.expanduser("~/.config/cascade/keys.env")
    keys = open(key_env, encoding="utf-8").read() if os.path.exists(key_env) else ""
    if not re.search(rf"^{key_ref}=\S+", keys, re.M):
        print(f"WARN: {key_ref} не задан в {key_env}", file=sys.stderr)

    print(f"OK: {slug} сконфигурирован: " + ", ".join(f"{r}={models[r]}" for r in required))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
