#!/usr/bin/env python3
"""roadmap.py — the architect turns a product vision into a staged roadmap of board cards.

Two phases, because one call produces either a shallow list or an unreadable dump:
  1. outline — the architect splits the product into stages, ordered from the simplest
     task (the one that is easiest to debug the cascade on) to the hardest.
  2. expansion — one call per stage turns the stage into detailed, general tasks.

CLI:
  roadmap.py --project <slug> --request "text|@file" [--repo PATH] [--out FILE]
             [--stages 10] [--workers 4]

Output (stdout, one JSON object):
  {"project": "<slug>", "generated_at": "...", "outline": {...}, "roadmap": {...},
   "validation": {"ok": bool, "errors": []}, "usage": [...]}

State:
  ~/.local/state/cascade/<slug>/roadmap/<ts>.json and roadmap/roadmap.json

Exit codes:
  0 — a valid roadmap was produced and written
  1 — the run failed (gateway error, bad input, invalid roadmap)
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))

from plan_task import (  # noqa: E402  — the gateway plumbing lives in one place
    HOUSE_RULES,
    STATE,
    call_model,
    extract_json,
    project_config,
    repo_map,
)

MAX_REQUEST_CHARS = 30000

OUTLINE_PROMPT = (
    """You are the architect of an automated development pipeline. You are not
writing code and not writing a detailed implementation plan: you are laying out the
development roadmap of a product as stages, ordered so that the first tasks are the easiest
possible to debug a multi-agent pipeline on and the last ones are the hardest and most
valuable.

Answer with exactly one JSON object and nothing else:

{"status": "ready",
 "product_summary": "3-5 sentences: what the product is and what decisions it enables",
 "constraints": ["hard constraints you will respect in every stage"],
 "stages": [
   {"id": "s0", "title": "<short stage title>", "intent": "what this stage delivers",
    "complexity": 1,
    "why_here": "why this stage sits at this position in the order",
    "exit_criteria": ["observable state of the product when the stage is done"],
    "optional": false,
    "task_titles": ["<title of a task in this stage>", "..."]}
 ],
 "designer_brief": {"title": "...", "goal": "...", "must_cover": ["..."]}}

Rules:
- 8..12 stages, numbered s0, s1, ... in execution order.
- Stage s0 and s1 are pipeline calibration: their tasks must be small, offline (no network,
  no ESI, no UI), single-purpose, with deterministic tests — the kind of task where a broken
  phase of the pipeline is obvious. Say so in `why_here`.
- Complexity grows monotonically (1..5). A stage may not be easier than the one before it
  unless it is marked "optional": true.
- The owner's own feature list is the backbone; do not drop any item of it. Do not invent a
  product that contradicts it.
- The LAST stage is "optional": true and holds your own proposals beyond the owner's brief
  (3..6 of them) — useful features the owner did not ask for, each justified in one phrase.
- Every stage has 3..8 tasks; task_titles are short imperative phrases (max 90 characters).
- `designer_brief` is the brief for a separate designer card: what the visual and interaction
  design must decide before the first UI task starts (grid, states, component inventory,
  token changes, accessibility, what the owner sees on each screen).
- Stages are vertical slices of the product, not layers: each stage ends with something the
  owner can see and use, or with data the next stage needs.

Write all human-facing text in RUSSIAN (the owner reads Russian); keep code identifiers,
ESI scope names and technology names in English."""
    + HOUSE_RULES
)

STAGE_PROMPT = (
    """You are the architect of an automated development pipeline, expanding one
stage of a product roadmap into detailed but GENERAL tasks — cards for a kanban board, not
subtask-level implementation plans. A card says what to build, why, what is in and out of
scope, what observable result proves it, and what it depends on. It does not name functions,
files or tests.

Answer with exactly one JSON object and nothing else:

{"status": "ready",
 "stage": {"id": "s2", "summary": "2-3 sentences"},
 "tasks": [
   {"id": "s2-t1",
    "title": "<imperative, max 90 characters>",
    "goal": "2-4 sentences: what is built and what decision it enables for the player",
    "scope": ["3-6 bullets: what is inside"],
    "out_of_scope": ["1-3 bullets: what is deliberately postponed"],
    "acceptance": ["2-5 observable results, checkable without reading the code"],
    "depends_on": ["ids of tasks that must land first"],
    "size": "S|M|L",
    "difficulty": 1,
    "roles": ["specifier", "executor"],
    "esi": {"endpoints": ["..."], "scopes": ["..."]},
    "risks": ["1-3 known risks or unknowns, if any"],
    "why_first": "why this task is a good debug exercise for the pipeline (only for
                  difficulty <= 2, otherwise empty string)"}
 ]}

Rules:
- 3..8 tasks. Ids are unique inside the whole roadmap, lowercase, `s<N>-t<M>`.
- size S = one sitting, M = 1-2 days, L = 3-5 days of pipeline work. difficulty 1..5.
- Add "designer" to `roles` when the task needs interaction or visual design before code.
- `esi` lists the real EVE ESI endpoints and scope strings the task touches; use [] and
  "esi": {"endpoints": [], "scopes": []} when the task touches no EVE API. Never invent a
  scope string — the shape is `esi-<area>.read_<thing>.v1` and only names you are sure of.
- If a fact constrains the task (ESI cannot place market orders, corporation endpoints need a
  corporation role, licensing forbids shipping CCP data), put it in `risks` or `scope`.
- depends_on may reference tasks of earlier stages; never the same stage after this one.
- Write in RUSSIAN; code identifiers, ESI scopes and technology names stay in English."""
    + HOUSE_RULES
)


def load_request(value: str) -> str:
    if value.startswith("@"):
        return Path(value[1:]).read_text(encoding="utf-8")
    return value


def call_json(cfg: dict, system: str, user: str, feature: str, task_id: str) -> tuple[dict, dict]:
    content, usage = call_model(cfg, system, user)
    answer = extract_json(content)
    record(cfg, usage, feature, task_id)
    return answer, usage


def record(cfg: dict, usage: dict, feature: str, task_id: str) -> None:
    if os.environ.get("ROADMAP_NO_LEDGER") == "1":
        return
    cmd = [
        sys.executable,
        str(BIN / "usage.py"),
        "record",
        "-p",
        cfg["slug"],
        "-f",
        feature,
        "-t",
        task_id,
        "-r",
        "architect",
        "-m",
        cfg["model"],
        "--input",
        str(int(usage.get("prompt_tokens") or 0)),
        "--output",
        str(int(usage.get("completion_tokens") or 0)),
        "--note",
        "roadmap.py",
    ]
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    if cached:
        cmd += ["--cache-read", str(cached)]
    subprocess.run(cmd, capture_output=True, text=True)


def validate(roadmap: dict) -> list[str]:
    """Deterministic checks: the board must not receive a broken dependency graph."""
    errors: list[str] = []
    stages = roadmap.get("stages") or []
    if not 6 <= len(stages) <= 14:
        errors.append(f"expected 8..12 stages, got {len(stages)}")
    seen: dict[str, dict] = {}
    ids: set[str] = set()
    for index, stage in enumerate(stages):
        sid = stage.get("id") or ""
        if not sid:
            errors.append(f"stage #{index} has no id")
            continue
        if sid in ids:
            errors.append(f"duplicate stage id {sid}")
        ids.add(sid)
        tasks = stage.get("tasks") or []
        if not 2 <= len(tasks) <= 10:
            errors.append(f"{sid}: expected 3..8 tasks, got {len(tasks)}")
        for task in tasks:
            tid = task.get("id") or ""
            if not tid:
                errors.append(f"{sid}: a task has no id")
                continue
            if tid in seen:
                errors.append(f"duplicate task id {tid}")
            seen[tid] = task
            for field, kind in (
                ("title", str),
                ("goal", str),
                ("size", str),
                ("scope", list),
                ("acceptance", list),
                ("roles", list),
            ):
                if not isinstance(task.get(field), kind):
                    errors.append(f"{tid}: field {field!r} is missing or of the wrong type")
            if task.get("size") not in ("S", "M", "L"):
                errors.append(f"{tid}: size must be S, M or L (got {task.get('size')!r})")
            difficulty = task.get("difficulty")
            if not isinstance(difficulty, int) or not 1 <= difficulty <= 5:
                errors.append(f"{tid}: difficulty must be an integer 1..5")
    # dependencies: exist, and acyclic
    graph = {tid: [d for d in (t.get("depends_on") or [])] for tid, t in seen.items()}
    for tid, deps in graph.items():
        for dep in deps:
            if dep not in graph:
                errors.append(f"{tid}: depends on unknown task {dep}")
    state: dict[str, int] = {}

    def walk(node: str, trail: list[str]) -> None:
        if state.get(node) == 2:
            return
        if state.get(node) == 1:
            errors.append("dependency cycle: " + " -> ".join(trail + [node]))
            return
        state[node] = 1
        for dep in graph.get(node, []):
            if dep in graph:
                walk(dep, trail + [node])
        state[node] = 2

    for tid in graph:
        walk(tid, [])
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Turn a product vision into a staged roadmap for the board.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--request", required=True, help="text or @path")
    parser.add_argument("--repo", help="repository path; defaults to ~/projects/<slug>")
    parser.add_argument("--out", help="also write the roadmap here")
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()

    request = load_request(args.request).strip()[:MAX_REQUEST_CHARS]
    if len(request) < 200:
        print("ERROR: the request is too short for a roadmap", file=sys.stderr)
        return 1

    repo = Path(os.path.expanduser(args.repo or f"~/projects/{args.project}"))
    cfg = project_config(args.project)
    cfg["slug"] = args.project
    map_doc = repo_map(repo)

    outline_user = (
        "CUSTOMER REQUEST (the owner's own words and the product brief)\n"
        f"{request}\n\n"
        "REPOSITORY MAP (what already exists; the roadmap starts from this state)\n"
        f"{map_doc}\n"
    )
    outline, _ = call_json(cfg, OUTLINE_PROMPT, outline_user, "_roadmap", "_outline")
    if outline.get("status") != "ready":
        print(f"ERROR: outline refused: {json.dumps(outline)[:500]}", file=sys.stderr)
        return 1
    stages = outline.get("stages") or []
    if not stages:
        print("ERROR: the outline has no stages", file=sys.stderr)
        return 1

    # Compact context for the expansion calls: the repo map is not repeated ten times.
    context = (
        "PRODUCT SUMMARY\n" + str(outline.get("product_summary", "")) + "\n\n"
        "HARD CONSTRAINTS\n" + json.dumps(outline.get("constraints") or [], ensure_ascii=False) + "\n\n"
        "CUSTOMER REQUEST\n" + request + "\n\n"
        "THE WHOLE ROADMAP, AS PLANNED (keep ids and ordering consistent with it)\n"
        + json.dumps(stages, ensure_ascii=False, indent=1)
        + "\n\nREPOSITORY TOP LEVEL\n"
        + "\n".join(
            sorted(
                {
                    ln.split(" (")[0].split("/")[0]
                    for ln in map_doc.splitlines()
                    if " (" in ln and not ln.startswith(" ")
                }
            )
        )
    )

    def expand(stage: dict) -> dict:
        user = (
            f"EXPAND STAGE {stage.get('id')}: {stage.get('title')}\n"
            f"Stage intent: {stage.get('intent')}\n"
            f"Why it sits here: {stage.get('why_here')}\n"
            f"Exit criteria: {json.dumps(stage.get('exit_criteria') or [], ensure_ascii=False)}\n"
            f"Planned task titles: {json.dumps(stage.get('task_titles') or [], ensure_ascii=False)}\n"
            f"Optional stage: {bool(stage.get('optional'))}\n\n" + context
        )
        answer, usage = call_json(cfg, STAGE_PROMPT, user, f"_roadmap:{stage.get('id')}", str(stage.get("id")))
        return {"stage": stage, "answer": answer, "usage": usage}

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        expanded = list(pool.map(expand, stages))

    merged_stages = []
    usage_log = []
    for item in expanded:
        stage = dict(item["stage"])
        answer = item["answer"]
        stage["tasks"] = answer.get("tasks") or []
        if answer.get("stage", {}).get("summary"):
            stage["summary"] = answer["stage"]["summary"]
        merged_stages.append(stage)
        usage_log.append({"stage": stage.get("id"), "usage": item["usage"]})

    roadmap = {
        "product_summary": outline.get("product_summary", ""),
        "constraints": outline.get("constraints") or [],
        "assumptions": outline.get("assumptions") or [],
        "stages": merged_stages,
        "designer_brief": outline.get("designer_brief") or {},
    }
    errors = validate(roadmap)

    result = {
        "project": args.project,
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "model": cfg["model"],
        "roadmap": roadmap,
        "validation": {"ok": not errors, "errors": errors},
        "usage": usage_log,
    }

    state_dir = STATE / args.project / "roadmap"
    state_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(result, ensure_ascii=False, indent=1)
    (state_dir / f"{stamp}.json").write_text(payload + "\n", encoding="utf-8")
    (state_dir / "roadmap.json").write_text(payload + "\n", encoding="utf-8")
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")

    print(payload)
    if errors:
        for error in errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
