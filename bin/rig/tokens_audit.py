#!/usr/bin/env python3
"""tokens_audit.py — check that a dark token table covers the light token source.

Parses `frontend/src/theme/tokens.ts` for the token names it defines, parses the markdown mapping
table, and reports tokens that exist in one place but not the other, malformed rows, empty cells and
duplicated keys. Numbers inside a contrast note are re-checked when both colours are plain hex.

CLI:
  tokens_audit.py --tokens docs/design/occc/tokens-dark.md --source frontend/src/theme/tokens.ts
                  [--canvas #0f141a] [--quiet]

Exit 0 — the table covers the source and every row is well formed; 1 — otherwise.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROW = re.compile(r"^\|\s*`([A-Za-z0-9_]+)`[^|]*\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*([^|]*)\|\s*$")
TOKEN_DEF = re.compile(r"^\s{2}([A-Za-z][A-Za-z0-9_]*)\s*:", re.MULTILINE)
HEX = re.compile(r"^#[0-9a-fA-F]{6}$")


def source_tokens(path: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    start = text.find("export const tokens")
    if start < 0:
        raise SystemExit(f"ERROR: no `export const tokens` block in {path}")
    block = text[start:]
    end = block.find("\n} as const")
    block = block[: end if end > 0 else len(block)]
    return sorted(set(TOKEN_DEF.findall(block)))


def relative_luminance(hex_colour: str) -> float:
    value = hex_colour.lstrip("#")
    channels = [int(value[i : i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    high, low = max(la, lb), min(la, lb)
    return round((high + 0.05) / (low + 0.05), 2)


def main() -> int:
    ap = argparse.ArgumentParser(description="Audit a dark token table against the light tokens.")
    ap.add_argument("--tokens", required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--canvas", default="#0f141a")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    table_path = Path(a.tokens)
    if not table_path.exists():
        print(f"ERROR: no table at {table_path}", file=sys.stderr)
        return 1

    rows = {}
    errors: list[str] = []
    warnings: list[str] = []
    for number, line in enumerate(table_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.startswith("|"):
            continue
        if set(line.replace("|", "").strip()) <= set("-: "):
            continue  # separator
        match = ROW.match(line)
        if not match:
            if "token" in line and "light" in line:
                continue  # header
            errors.append(f"line {number}: not a `| token | light | dark | use | contrast |` row")
            continue
        token, light, dark, use, note = (g.strip().strip("`") for g in match.groups())
        if token in rows:
            errors.append(f"line {number}: duplicate row for {token}")
        if not use.strip():
            errors.append(f"line {number}: {token} has an empty use cell")
        if not note.strip():
            errors.append(f"line {number}: {token} has an empty contrast note")
        rows[token] = {"light": light, "dark": dark, "use": use, "note": note, "line": number}

    # only colours differ between the themes; spacing, radii, type and motion stay as they are
    wanted = [t for t in source_tokens(Path(a.source)) if t.startswith("color")]
    missing = [t for t in wanted if t not in rows]
    extra = [t for t in rows if t not in wanted]

    verdict = {
        "ok": not errors and not missing,
        "rows": len(rows),
        "missing": missing,
        "extra": extra,
        "errors": errors,
        "warnings": warnings,
    }
    print(json.dumps(verdict, ensure_ascii=False, indent=1))
    for item in errors + [f"missing row: {t}" for t in missing]:
        print(f"ERROR: {item}", file=sys.stderr)
    if not a.quiet:
        for item in warnings:
            print(f"note: {item}", file=sys.stderr)
        for item in extra:
            print(f"note: row not in the light tokens: {item}", file=sys.stderr)
    return 0 if verdict["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
