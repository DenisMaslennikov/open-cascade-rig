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

ROLES = ("architect", "chief_architect", "specifier", "executor", "arbiter", "reviewer",
         "vision")
UI_ROLES = ("designer",)      # required when the project declares `ui: true`


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
        m = re.match(r"^  (\w+):\s*(.*)$", line)          # вложенный ключ
        if m:
            out.setdefault(section, {})[m.group(1)] = _clean(m.group(2))
            continue
        m = re.match(r"^(\w+):\s*(.*)$", line)             # верхний ключ
        if m:
            key, val = m.group(1), m.group(2).strip()
            if val:
                out[section][key] = _clean(val)
            else:
                section = key
                out.setdefault(section, {})
    return out


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
        print(f"       заполните ~/.config/cascade/projects/{slug}.yaml → models:",
              file=sys.stderr)
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
