#!/usr/bin/env python3
"""chief_architect.py — the owner asks the chief architect a question; the orchestrator picks the context.

This is not a pipeline phase: the architect role decomposes one task into subtasks under a strict
JSON schema, while the chief architect answers the owner in prose — decisions, trade-offs, risks.
The role is invoked by a human; which artifacts it sees is the orchestrator's decision, not the
model's.

CLI:
  chief_architect.py --project <slug> --request "text|@file"
                     [--context repo,schema,roadmap,file:PATH] [--repo PATH]
                     [--out FILE]

Context keys:
  repo      — repository map plus AGENTS.md, .agents/rules/*, ADR titles (plan_task.repo_map)
  schema    — the database-schema contract: conventions and every table with its columns
  roadmap   — the product roadmap: stages and task titles with a one-line goal
  file:PATH — one file, verbatim (up to 20k characters)

Output (stdout, one JSON object):
  {"status": "ready", "answer": "...", "context": {...}, "usage": {...}, "artifact": "..."}

State:
  ~/.local/state/cascade/<slug>/advice/<timestamp>.json and advice/latest.md

Exit codes: 0 — an answer was produced, 1 — failure.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .plan_task import (
    CONFIG,
    HOUSE_RULES,
    STATE,
    call_model_stream,
    load_keys,
    parse_simple_yaml,
    repo_map,
    token_limit,
)
from .rig_conf import gateway

BIN = Path(__file__).resolve().parent
PKG_ROOT = BIN.parent

MAX_FILE_CHARS = 20000
MAX_REQUEST_CHARS = 20000

SYSTEM_PROMPT = (
    """You are the chief architect of this project. The owner asks you a question; you
answer it as the person responsible for the shape of the system, not as a task decomposer.

What a good answer contains:
- the decision, stated first, in one or two sentences;
- why it holds, grounded in the artifacts you were given — cite actual paths, tables, endpoints,
  role names; never invent a fact about the repository and never assume an artifact you did not see;
- what it costs: what it constrains later, what has to change if the opposite decision is taken;
- alternatives, only real ones, with the reason each loses;
- risks and unknowns, named, with the cheapest way to check each;
- the next concrete step, small enough to start now.

Rules:
- If the answer depends on a fact that is not in the provided context, say exactly which artifact
  is missing and what it would settle. Do not fill the gap with a plausible guess.
- Distinguish a decision that is cheap to reverse from one that is not; the second kind deserves
  an ADR and an explicit "это дорого отменить".
- Do not write code unless the owner asks for code. Pseudocode or a signature is fine when it
  removes ambiguity.
- Keep it as long as the question deserves and not longer. No preamble, no summary of the question.
- Write in RUSSIAN; identifiers, paths, table names, scopes and technology names stay in English."""
    + HOUSE_RULES
)


def role_config(slug: str) -> dict:
    """Model, key and sampling parameters of the chief architect role."""
    project = parse_simple_yaml(CONFIG / "projects" / f"{slug}.yaml")
    roles = parse_simple_yaml(CONFIG / "roles.yaml")
    model = (project.get("models") or {}).get("chief_architect", "")
    if not model:
        raise SystemExit(f"ERROR: chief_architect model is not set for {slug}")
    key_ref = project.get("_", {}).get("key_ref") or "LITELLM_KEY"
    key = load_keys().get(key_ref, "")
    if not key:
        raise SystemExit(f"ERROR: {key_ref} is missing from {CONFIG / 'keys.env'}")
    params = (roles.get("roles") or {}).get("chief_architect", {})
    return {
        "model": model,
        "key": key,
        "base_url": gateway(project, script="chief_architect.py"),
        "temperature": float(params.get("temperature", 0.2)),
        "max_tokens": token_limit(params.get("max_tokens")),
    }


def schema_digest(slug: str) -> str:
    path = STATE / slug / "schema" / "schema.json"
    if not path.exists():
        return ""
    schema = json.loads(path.read_text(encoding="utf-8")).get("schema") or {}
    lines = ["DATABASE SCHEMA CONTRACT", ""]
    for convention in schema.get("conventions") or []:
        lines.append(f"- {convention}")
    for agg in schema.get("aggregates") or []:
        lines += [
            "",
            f"## aggregate {agg.get('id')} — {agg.get('title')} "
            f"({agg.get('lifetime')}), owner {agg.get('owner_module')}",
        ]
        for table in agg.get("tables") or []:
            cols = ", ".join(
                f"{c['name']} {c['type']}"
                + (" pk" if c.get("pk") else "")
                + (" null" if c.get("nullable") else "")
                + (
                    f" -> {c['references']['table']}.{c['references']['column']}"
                    if (c.get("references") or {}).get("table")
                    else ""
                )
                for c in table.get("columns") or []
            )
            lines.append(f"  {table['name']}: {cols}")
            if table.get("indexes"):
                lines.append(
                    "    indexes: "
                    + "; ".join(
                        f"({', '.join(i.get('columns') or [])})" + (" unique" if i.get("unique") else "")
                        for i in table["indexes"]
                    )
                )
            if table.get("constraints"):
                lines.append("    constraints: " + "; ".join(table["constraints"]))
            if table.get("growth"):
                lines.append("    growth: " + str(table["growth"]))
    return "\n".join(lines)


def roadmap_digest(slug: str) -> str:
    path = STATE / slug / "roadmap" / "roadmap.json"
    if not path.exists():
        return ""
    roadmap = json.loads(path.read_text(encoding="utf-8")).get("roadmap") or {}
    lines = ["PRODUCT ROADMAP", "", str(roadmap.get("product_summary", "")).strip(), ""]
    for stage in roadmap.get("stages") or []:
        lines.append(
            f"## {stage.get('id')} — {stage.get('title')} (complexity "
            f"{stage.get('complexity')}): {stage.get('intent', '')}"
        )
        for task in stage.get("tasks") or []:
            goal = " ".join(str(task.get("goal", "")).split())[:300]
            lines.append(f"  - {task.get('id')} [{task.get('size')}] {task.get('title')}: {goal}")
    return "\n".join(lines)


def build_context(keys: list[str], repo: Path, slug: str) -> tuple[str, dict]:
    parts: list[str] = []
    report: dict[str, int] = {}
    for key in keys:
        if key == "repo":
            text = repo_map(repo)
            parts.append("REPOSITORY MAP, RULES AND ADR TITLES\n" + text)
            report["repo"] = len(text)
        elif key == "schema":
            text = schema_digest(slug)
            if text:
                parts.append(text)
                report["schema"] = len(text)
            else:
                report["schema"] = 0
        elif key == "roadmap":
            text = roadmap_digest(slug)
            if text:
                parts.append(text)
                report["roadmap"] = len(text)
            else:
                report["roadmap"] = 0
        elif key.startswith("file:"):
            path = Path(os.path.expanduser(key[5:]))
            if not path.is_absolute():
                path = repo / path
            if not path.exists():
                report[key] = 0
                continue
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
            parts.append(f"FILE {path}\n{text}")
            report[key] = len(text)
        else:
            raise SystemExit(f"ERROR: unknown context key {key!r}")
    return "\n\n".join(parts), report


def record(cfg: dict, slug: str, usage: dict) -> None:
    cmd = [
        sys.executable,
        "-m",
        "rig.usage",
        "record",
        "-p",
        slug,
        "-f",
        "_advice",
        "-t",
        "_chief_architect",
        "-r",
        "chief_architect",
        "-m",
        cfg["model"],
        "--input",
        str(int(usage.get("prompt_tokens") or 0)),
        "--output",
        str(int(usage.get("completion_tokens") or 0)),
        "--note",
        "chief_architect.py",
    ]
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    if cached:
        cmd += ["--cache-read", str(cached)]
    subprocess.run(cmd, capture_output=True, text=True, cwd=PKG_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the chief architect a question.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--request", required=True, help="text or @path")
    parser.add_argument("--context", default="repo", help="comma-separated: repo, schema, roadmap, file:PATH")
    parser.add_argument("--repo", help="repository path; defaults to ~/projects/<slug>")
    parser.add_argument("--out", help="also write the answer here")
    args = parser.parse_args()

    request = args.request
    if request.startswith("@"):
        request = Path(request[1:]).read_text(encoding="utf-8")
    request = request.strip()[:MAX_REQUEST_CHARS]
    if len(request) < 5:
        print("ERROR: the request is empty", file=sys.stderr)
        return 1

    repo = Path(os.path.expanduser(args.repo or f"~/projects/{args.project}"))
    cfg = role_config(args.project)
    keys = [k.strip() for k in args.context.split(",") if k.strip()]
    context, report = build_context(keys, repo, args.project)

    user = ("OWNER'S QUESTION\n" + request + "\n\n" + context).strip()
    content, usage = call_model_stream(cfg, SYSTEM_PROMPT, user, idle_timeout=300)
    if usage.get("_finish_reason") == "length":
        print(f"ERROR: the answer was cut at max_tokens={cfg['max_tokens']}", file=sys.stderr)
        return 1

    record(cfg, args.project, usage)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    state_dir = STATE / args.project / "advice"
    state_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": "ready",
        "asked_at": stamp,
        "model": cfg["model"],
        "request": request,
        "context": report,
        "usage": {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "cost": usage.get("cost"),
        },
        "answer": content,
    }
    file_stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (state_dir / f"{file_stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (state_dir / "latest.md").write_text(f"# Вопрос ({stamp})\n\n{request}\n\n---\n\n{content}\n", encoding="utf-8")
    if args.out:
        Path(os.path.expanduser(args.out)).write_text(content + "\n", encoding="utf-8")
    payload["artifact"] = str(state_dir / f"{file_stamp}.json")
    print(json.dumps(payload, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
