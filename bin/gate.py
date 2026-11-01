#!/usr/bin/env python3
"""gate.py — детерминированные гейты каскада (без ИИ).

Команды:
  check-diff   --repo PATH --allowed a,b,c [--base REF]
      Падает, если изменены файлы вне allowed. Без --base сравнивает рабочее
      дерево с HEAD; с --base — диапазон base...HEAD.
  expect-fail  --repo PATH --cmd "pytest -q" [--timeout 900]
      Падает, если команда вернула 0. Смысл: тесты обязаны падать на старом коде.
  expect-pass  --repo PATH --cmd "pytest -q" [--timeout 900]
      Падает, если команда вернула не 0. Смысл: код готов только когда зелено.
  frozen-tests --repo PATH --tests-prefix tests/ [--base REF]
      Падает, если изменены файлы под tests-prefix (фаза «код» идёт при
      замороженных тестах).

Все команды печатают JSON в stdout и возвращают 0 (гейт пройден) или 1 (нет).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess


def run(cmd: str, cwd: str, timeout: int) -> tuple[int, str]:
    try:
        p = subprocess.run(cmd, cwd=cwd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout + p.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"timeout after {timeout}s"


def changed_files(repo: str, base: str | None) -> list[str]:
    rng = [f"{base}...HEAD"] if base else []
    if rng:
        cmd = ["git", "diff", "--name-only", *rng]
    else:
        cmd = ["git", "status", "--porcelain"]
    p = subprocess.run(cmd, cwd=repo, capture_output=True, text=True)
    if p.returncode != 0:
        raise SystemExit(f"git failed: {p.stderr.strip()}")
    out = p.stdout.splitlines()
    if rng:
        return [l.strip() for l in out if l.strip()]
    files = []
    for line in out:
        if not line.strip():
            continue
        path = line[3:].strip()
        if " -> " in path:  # rename
            path = path.split(" -> ")[-1]
        files.append(path)
    return files


def verdict(ok: bool, payload: dict) -> int:
    payload["ok"] = ok
    print(json.dumps(payload, ensure_ascii=False))
    return 0 if ok else 1


def main() -> int:
    ap = argparse.ArgumentParser(add_help=True)
    ap.add_argument("command", choices=["check-diff", "expect-fail", "expect-pass", "frozen-tests"])
    ap.add_argument("--repo", required=True)
    ap.add_argument("--allowed", default="")
    ap.add_argument("--base", default=None)
    ap.add_argument("--cmd", default="")
    ap.add_argument("--timeout", type=int, default=900)
    ap.add_argument("--tests-prefix", default="tests/")
    ap.add_argument("--marker", default=None, help="regex, доказывающий, что падение — по ассерту (expect-fail)")
    a = ap.parse_args()

    if a.command == "check-diff":
        allowed = [x.strip() for x in a.allowed.split(",") if x.strip()]
        files = changed_files(a.repo, a.base)
        bad = [f for f in files if not any(f == x or f.startswith(x.rstrip("/") + "/") for x in allowed)]
        return verdict(not bad, {"gate": "check-diff", "changed": files, "allowed": allowed, "violations": bad})

    if a.command == "frozen-tests":
        files = changed_files(a.repo, a.base)
        bad = [f for f in files if f.startswith(a.tests_prefix)]
        return verdict(not bad, {"gate": "frozen-tests", "changed": files, "prefix": a.tests_prefix, "violations": bad})

    if not a.cmd:
        raise SystemExit("--cmd обязателен для expect-*")
    code, out = run(a.cmd, a.repo, a.timeout)
    tail = "\n".join(out.strip().splitlines()[-25:])

    infra = any(s in out for s in ("No module named", "command not found", "not found in $PATH"))
    if a.command == "expect-fail":
        # «тесты падают» — это падение по ассерту, а не «pytest не установлен».
        marker = a.marker or r"(?i)(assert|failed|failure|error|traceback)"
        matched = re.search(marker, out) is not None
        ok = code != 0 and matched and not infra
        return verdict(
            ok,
            {
                "gate": "expect-fail",
                "exit_code": code,
                "matched_marker": matched,
                "infrastructure_error": infra,
                "tail": tail,
            },
        )
    return verdict(
        code == 0 and not infra, {"gate": "expect-pass", "exit_code": code, "infrastructure_error": infra, "tail": tail}
    )


if __name__ == "__main__":
    raise SystemExit(main())
