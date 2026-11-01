#!/usr/bin/env python3
"""schema_design.py — the architect designs the database-schema contract, and DBML comes out of it.

Two phases, because one call over a whole product schema produces either a sketch or a wall:
  1. outline — aggregates (bounded contexts), their tables, enums and global conventions;
  2. expansion — one call per aggregate with exact columns, types, keys, indexes and cascades.

Output artifacts (all under the state dir unless --out-dir):
  schema.json          canonical contract (machine-readable, feeds schema_gate.py)
  schema.dbml          ERD for dbdiagram.io
  schema.md            human digest
  contract/<agg>.json  per-aggregate slice (the future docs/schema/<agg>.yaml)

CLI:
  schema_design.py --project <slug> --request @file [--roadmap FILE] [--repo PATH]
                   [--out-dir DIR] [--workers 4]

Exit codes: 0 — a valid contract was produced, 1 — failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

BIN = Path(__file__).resolve().parent
from .plan_task import (  # noqa: E402
    HOUSE_RULES,
    STATE,
    call_model_stream,
    extract_json,
    project_config,
    repo_map,
)

BAD_MONEY_TYPES = {"float", "float4", "float8", "double", "double precision", "real"}

OUTLINE_PROMPT = (
    """You are the data architect of an application built by an automated
development pipeline. You design the DATABASE-SCHEMA CONTRACT: aggregates (bounded contexts),
their tables, and the conventions every later task must follow. You are not writing SQLAlchemy
code, migrations or queries.

Answer with exactly one JSON object and nothing else. This answer is a SKELETON: detailed
columns come in separate calls, so keep it compact.

{"status": "ready",
 "conventions": ["one rule per line, max 20 words"],
 "enums": [{"name": "order_kind", "values": ["buy", "sell"]}],
 "aggregates": [
   {"id": "accounts",
    "title": "Accounts, characters, tokens",
    "purpose": "max 15 words",
    "owner_module": "backend/src/app/dbhelpers/accounts.py",
    "lifetime": "user",
    "tables": ["accounts", "characters"],
    "relations": ["characters.account_id -> accounts.id, cascade"],
    "growth": "max 10 words, empty when the table set is small"}],
 "migration_order": [{"aggregate": "accounts", "tables": ["accounts", "characters"],
                      "why_together": "max 20 words"}]}

Budget: the whole answer must stay under 4000 tokens. `tables` are bare names, not objects —
no per-table purpose, no columns, no types in this answer.

Rules:
- 8..14 aggregates. Together they cover every requirement in the brief; nothing extra.
- one aggregate == one `dbhelpers/<aggregate>.py` module and one owning task.
- `lifetime` is exactly one of: "user" (rows belong to a person and must die with the account),
  "reference" (game data imported from the SDE), "cache" (ESI responses, disposable).
- table names: snake_case, plural, ASCII, no prefixes; column names snake_case; foreign keys
  `<entity>_id`; timestamps `created_at` / `updated_at`; money `numeric(20,2)`.
- game identifiers (`character_id`, `type_id`, `corporation_id`, `structure_id`, `alliance_id`)
  are foreign keys to nothing in our database, but they are naturally unique — say where.
- every user-lifetime table must reach `accounts.id` through a chain of cascading foreign keys.
- name the growth control for big tables (unique key for upsert, retention, partition key) in
  `growth`, not in prose.
- no lookup tables that replace SDE; no EAV; no polymorphic `entity_id`.
- Table purposes, column comments and growth notes are written in ENGLISH: the contract is a
  repository artefact, not a chat message. Identifiers and types are English too."""
    + HOUSE_RULES
)

EXPAND_PROMPT = (
    """You are the data architect of an automated pipeline. Expand exactly ONE
aggregate of an already agreed schema outline into complete table definitions.

Answer with exactly one JSON object:

{"status": "ready",
 "aggregate": "accounts",
 "tables": [
   {"name": "characters",
    "purpose": "one line",
    "columns": [
      {"name": "id", "type": "bigint", "pk": true, "nullable": false, "unique": false,
       "default": "", "references": {"table": "accounts", "column": "id", "on_delete": "cascade"},
       "comment": ""}],
    "indexes": [{"name": "ix_characters_account", "columns": ["account_id"], "unique": false}],
    "constraints": ["unique (account_id, character_id)"],
    "growth": ""}],
 "notes": ["anything the implementer must know and cannot infer"]}

Rules:
- PostgreSQL 16 types only: bigint, integer, smallint, text, varchar(n), boolean, timestamptz,
  date, numeric(p,s), jsonb, jsonb[], uuid, bytea, bigint[], integer[]. Never float/double/real
  for money, quantity or price.
- every table: `id bigint` primary key, `created_at timestamptz not null default now()`; add
  `updated_at timestamptz not null default now()` when rows mutate.
- natural keys get real UNIQUE constraints, not application checks (character_id, type_id,
  order_id, killmail_id, mail_id, job_id...).
- foreign keys may point to tables of other aggregates — use the exact table and column names from
  the outline; `on_delete` is one of: cascade, restrict, set null, no action.
- JSONB only for opaque external payloads (ESI bodies, ESI cache) — never for product fields.
- indexes: only for the queries the brief actually needs; an index without a query is a defect.
- big tables: state upsert key / retention / partition in `growth`.
- `comment` only where the column name alone is not enough.
- Table purposes, column comments and growth notes are written in ENGLISH: the contract is a
  repository artefact. Identifiers and types are English too."""
    + HOUSE_RULES
)


def load_request(value: str) -> str:
    return Path(value[1:]).read_text(encoding="utf-8") if value.startswith("@") else value


def record(cfg: dict, usage: dict, feature: str, task_id: str) -> None:
    if os.environ.get("SCHEMA_NO_LEDGER") == "1":
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
        "schema_design.py",
    ]
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    if cached:
        cmd += ["--cache-read", str(cached)]
    subprocess.run(cmd, capture_output=True, text=True)


def call_json(cfg: dict, system: str, user: str, feature: str, task_id: str, raw_dir: Path | None = None) -> dict:
    content, usage = call_model_stream(cfg, system, user, idle_timeout=240)
    if raw_dir:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{task_id.replace(':', '_')}.txt").write_text(content, encoding="utf-8")
    if usage.get("_finish_reason") == "length":
        raise SystemExit(
            f"ERROR: {task_id}: the answer was cut at max_tokens="
            f"{cfg['max_tokens']}; the stage asks for too much in one response"
        )
    answer = extract_json(content)
    record(cfg, usage, feature, task_id)
    return answer


def roadmap_context(path: Path) -> str:
    """Distil the roadmap into the data requirements the schema must satisfy."""
    if not path.exists():
        return ""
    data = json.loads(path.read_text(encoding="utf-8"))
    roadmap = data.get("roadmap", data)
    lines = ["PRODUCT SUMMARY", str(roadmap.get("product_summary", "")).strip(), "", "HARD CONSTRAINTS"]
    lines += [f"- {item}" for item in roadmap.get("constraints") or []]
    lines += ["", "STAGES AND TASKS (what the schema has to serve)"]
    for stage in roadmap.get("stages") or []:
        lines += ["", f"## {stage.get('id')} — {stage.get('title')}: {stage.get('intent', '')}"]
        for task in stage.get("tasks") or []:
            goal = re.sub(r"\s+", " ", str(task.get("goal", ""))).strip()
            lines.append(f"- {task.get('id')} [{task.get('size')}] {task.get('title')}: {goal[:400]}")
    return "\n".join(lines)


def normalise(aggregates: list[dict], expanded: list[dict]) -> tuple[dict, list[str]]:
    """Merge the outline with the expanded tables; return the schema and the gaps."""
    by_id = {a["id"]: dict(a) for a in aggregates}
    missing: list[str] = []
    for item in expanded:
        answer = item["answer"]
        agg = by_id.get(item["aggregate_id"])
        if not agg:
            continue
        tables = [t for t in answer.get("tables") or [] if isinstance(t, dict) and t.get("name")]
        agg["tables"] = tables
        agg["notes"] = answer.get("notes") or []
        if not tables:
            missing.append(item["aggregate_id"])
    for agg in by_id.values():
        if not isinstance(agg.get("tables"), list) or any(not isinstance(t, dict) for t in agg["tables"]):
            agg["tables"] = [t for t in agg.get("tables") or [] if isinstance(t, dict)]
            if agg["id"] not in missing:
                missing.append(agg["id"])
    return {"aggregates": [by_id[a["id"]] for a in aggregates]}, missing


def validate(schema: dict) -> tuple[list[str], list[str]]:
    """Return (errors, warnings). Errors mean the contract is unusable as a gate."""
    errors: list[str] = []
    warnings: list[str] = []
    table_owner: dict[str, str] = {}
    columns: dict[str, dict[str, dict]] = {}
    for agg in schema["aggregates"]:
        if not agg.get("tables"):
            errors.append(f"aggregate {agg.get('id')}: no tables")
        for table in agg.get("tables") or []:
            if not isinstance(table, dict):
                errors.append(f"aggregate {agg.get('id')}: a table entry is not an object")
                continue
            name = table.get("name") or ""
            if not re.fullmatch(r"[a-z][a-z0-9_]*", name):
                errors.append(f"table name {name!r} is not snake_case ASCII")
            if name in table_owner:
                errors.append(f"table {name} declared twice ({table_owner[name]}, {agg['id']})")
            table_owner[name] = agg["id"]
            cols = table.get("columns") or []
            if not cols:
                errors.append(f"table {name}: no columns")
            seen: dict[str, dict] = {}
            for col in cols:
                cname = col.get("name") or ""
                if not re.fullmatch(r"[a-z][a-z0-9_]*", cname):
                    errors.append(f"{name}.{cname}: column name is not snake_case ASCII")
                if cname in seen:
                    errors.append(f"{name}.{cname}: duplicated column")
                seen[cname] = col
                ctype = str(col.get("type") or "").lower()
                if not ctype:
                    errors.append(f"{name}.{cname}: no type")
                if ctype in BAD_MONEY_TYPES:
                    errors.append(f"{name}.{cname}: floating-point type {ctype!r} is forbidden")
            columns[name] = seen
            if not any(c.get("pk") for c in cols):
                errors.append(f"table {name}: no primary key")
            if "created_at" not in seen:
                warnings.append(f"table {name}: no created_at")
            if not table.get("indexes") and not any(c.get("pk") for c in cols[:1]):
                warnings.append(f"table {name}: no indexes declared")
            for composite in table.get("foreign_keys") or []:
                cols = composite.get("columns") or []
                ref_table = composite.get("references_table")
                ref_cols = composite.get("references_columns") or []
                for cname in cols:
                    if cname not in seen:
                        errors.append(f"{name}: composite FK uses unknown column {cname!r}")
                target_cols = columns.get(ref_table)
                if not target_cols:
                    errors.append(f"{name}: composite FK references unknown table {ref_table!r}")
                else:
                    for cname in ref_cols:
                        if cname not in target_cols:
                            errors.append(f"{name}: composite FK references unknown column {ref_table}.{cname}")
                    wanted = {ref_table: tuple(ref_cols)}
                    declared = " ".join(
                        str(c)
                        for tb in schema["aggregates"]
                        for tb2 in tb.get("tables") or []
                        if tb2.get("name") == ref_table
                        for c in tb2.get("constraints") or []
                    )
                    for cname in ref_cols:
                        wanted.setdefault(ref_table, ())
                    if all(cname in declared for cname in ref_cols) is False:
                        warnings.append(
                            f"{name}: composite FK target {ref_table}{tuple(ref_cols)} is not declared unique"
                        )
            account_col = seen.get("account_id")
            if account_col:
                ref = account_col.get("references") or {}
                if ref.get("table") != "accounts":
                    errors.append(f"{name}.account_id must reference accounts.id")
                elif str(ref.get("on_delete", "")).lower() != "cascade":
                    errors.append(f"{name}.account_id must cascade on delete")
    for agg in schema["aggregates"]:
        for table in agg.get("tables") or []:
            for col in table.get("columns") or []:
                ref = col.get("references") or {}
                if not ref:
                    continue
                target_table, target_col = ref.get("table"), ref.get("column")
                if target_table not in columns:
                    errors.append(f"{table['name']}.{col['name']}: references unknown table {target_table!r}")
                elif target_col not in columns[target_table]:
                    errors.append(f"{table['name']}.{col['name']}: unknown column {target_table}.{target_col}")
    return errors, warnings


def dbml(schema: dict, groups: bool = True) -> str:
    out: list[str] = [
        "// OpenCapsuleerCommandCenter — database schema contract",
        "// generated by schema_design.py; edit the contract, not this file",
        "",
    ]
    for enum in schema.get("enums") or []:
        out.append(f"Enum {enum['name']} {{")
        for value in enum.get("values") or []:
            out.append("  " + re.sub(r"[^A-Za-z0-9_]", "_", str(value)))
        out += ["}", ""]
    if groups:
        for agg in schema["aggregates"]:
            members = " ".join(f'"{t["name"]}"' for t in agg.get("tables") or [])
            out += [f"TableGroup {agg['id']} {{", f"  {members}", "}", ""]
    refs: list[str] = []
    for agg in schema["aggregates"]:
        for table in agg.get("tables") or []:
            out.append(f'Table "{table["name"]}" {{')
            for col in table.get("columns") or []:
                attrs = []
                if col.get("pk"):
                    attrs.append("pk")
                if col.get("nullable") is False:
                    attrs.append("not null")
                if col.get("unique"):
                    attrs.append("unique")
                if col.get("default"):
                    raw_default = str(col["default"])
                    if "identity" in raw_default.lower():
                        attrs.append("increment")
                    else:
                        attrs.append(f"default: `{raw_default}`")
                if col.get("comment"):
                    attrs.append("note: '" + str(col["comment"]).replace("'", " ") + "'")
                suffix = f" [{', '.join(attrs)}]" if attrs else ""
                out.append(f'  "{col["name"]}" {col["type"]}{suffix}')
                ref = col.get("references") or {}
                if ref.get("table"):
                    on_delete = str(ref.get("on_delete") or "").lower()
                    rel = f'Ref: "{table["name"]}"."{col["name"]}" > "{ref["table"]}"."{ref.get("column", "id")}"'
                    if on_delete and on_delete != "no action":
                        rel += f" [delete: {on_delete}]"
                    refs.append(rel)
            for idx in table.get("indexes") or []:
                names = []
                for raw_name in idx.get("columns") or []:
                    bare = re.sub(r"\s+(asc|desc|nulls\s+\w+)$", "", str(raw_name), flags=re.I)
                    if bare in {c.get("name") for c in table.get("columns") or []}:
                        names.append(bare)
                if not names:
                    out.append(f"  // skipped index {idx.get('name', '')}: columns do not resolve")
                    continue
                cols = ", ".join(f'"{c}"' for c in names)
                attrs = [f"name: '{idx.get('name', '')}'"]
                if idx.get("unique"):
                    attrs.insert(0, "unique")
                out.append(f"  indexes {{ ({cols}) [{', '.join(attrs)}] }}")
            for composite in table.get("foreign_keys") or []:
                left = ", ".join(f'"{c}"' for c in composite.get("columns") or [])
                right = ", ".join(f'"{c}"' for c in composite.get("references_columns") or [])
                rel = f'Ref: "{table["name"]}".({left}) > "{composite.get("references_table")}".({right})'
                on_delete = str(composite.get("on_delete") or "").lower()
                if on_delete and on_delete != "no action":
                    rel += f" [delete: {on_delete}]"
                refs.append(rel)
            if table.get("purpose"):
                out.append("  Note: '" + str(table["purpose"]).replace("'", " ") + "'")
            out += ["}", ""]
    out += refs
    return "\n".join(out) + "\n"


def markdown(schema: dict) -> str:
    lines = ["# Контракт схемы БД OCCC", ""]
    lines += ["## Соглашения", ""] + [f"- {c}" for c in schema.get("conventions") or []]
    total = sum(len(a.get("tables") or []) for a in schema["aggregates"])
    lines += ["", f"Агрегатов: {len(schema['aggregates'])}, таблиц: {total}", ""]
    for agg in schema["aggregates"]:
        lines += [
            f"## {agg['id']} — {agg.get('title', '')}",
            "",
            str(agg.get("purpose", "")).strip(),
            "",
            f"`{agg.get('owner_module', '')}` · жизненный цикл: {agg.get('lifetime', '')}",
            "",
        ]
        for table in agg.get("tables") or []:
            lines += [
                f"### {table['name']}",
                "",
                str(table.get("purpose", "")).strip(),
                "",
                "| колонка | тип | ключ | ссылка |",
                "|---|---|---|---|",
            ]
            for col in table.get("columns") or []:
                key = "PK" if col.get("pk") else ("UQ" if col.get("unique") else "")
                ref = col.get("references") or {}
                ref_s = (
                    (f"→ {ref.get('table')}.{ref.get('column')} ({ref.get('on_delete')})") if ref.get("table") else ""
                )
                lines.append(f"| {col['name']} | {col['type']} | {key} | {ref_s} |")
            if table.get("indexes"):
                lines += [
                    "",
                    "Индексы: " + ", ".join(f"({', '.join(i.get('columns') or [])})" for i in table["indexes"]),
                ]
            if table.get("constraints"):
                lines += ["Ограничения: " + "; ".join(table["constraints"])]
            if table.get("growth"):
                lines += ["Рост: " + table["growth"]]
            lines.append("")
    return "\n".join(lines) + "\n"


def generate(
    cfg: dict, brief: str, requirements: str, repo: Path, out_dir: Path, workers: int
) -> tuple[dict, list[str], list[str]]:
    """Outline then per-aggregate expansion; returns (schema, errors, warnings)."""
    base = (
        "TASK BRIEF (the customer's own statement of the schema contract)\n"
        f"{brief}\n\n"
        + (requirements + "\n\n" if requirements else "")
        + "REPOSITORY MAP (existing structure and rules)\n"
        + repo_map(repo)
    )

    raw_dir = out_dir / "raw"
    outline = call_json(cfg, OUTLINE_PROMPT, base, "_schema", "_outline", raw_dir)
    if outline.get("status") != "ready":
        raise SystemExit(f"ERROR: outline refused: {json.dumps(outline)[:400]}")
    aggregates = outline.get("aggregates") or []
    if not 4 <= len(aggregates) <= 18:
        raise SystemExit(f"ERROR: outline produced {len(aggregates)} aggregates")

    shared = (
        "GLOBAL CONVENTIONS (already agreed, do not contradict)\n"
        + json.dumps(outline.get("conventions") or [], ensure_ascii=False)
        + "\n\nENUMS\n"
        + json.dumps(outline.get("enums") or [], ensure_ascii=False)
        + "\n\nTHE WHOLE OUTLINE (use these exact table and column names)\n"
        + json.dumps(
            [{k: a.get(k) for k in ("id", "title", "purpose", "lifetime", "tables", "relations")} for a in aggregates],
            ensure_ascii=False,
        )
    )

    def expand(agg: dict) -> dict:
        user = (
            f"EXPAND AGGREGATE {agg['id']} — {agg.get('title')}\n"
            f"Purpose: {agg.get('purpose')}\n"
            f"Lifetime: {agg.get('lifetime')}\n"
            f"Tables planned: {json.dumps(agg.get('tables') or [], ensure_ascii=False)}\n"
            f"Relations: {json.dumps(agg.get('relations') or [], ensure_ascii=False)}\n\n"
            + shared
            + "\n\nTASK BRIEF\n"
            + brief
        )
        answer = call_json(cfg, EXPAND_PROMPT, user, f"_schema:{agg['id']}", agg["id"], raw_dir)
        return {"aggregate_id": agg["id"], "answer": answer}

    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        expanded = list(pool.map(expand, aggregates))

    schema, missing = normalise(aggregates, expanded)
    schema["conventions"] = outline.get("conventions") or []
    schema["enums"] = outline.get("enums") or []
    schema["migration_order"] = outline.get("migration_order") or []
    schema["product_summary"] = outline.get("product_summary", "")

    errors, warnings = validate(schema)
    for agg_id in missing:
        errors.append(f"aggregate {agg_id}: no tables came back, the contract is incomplete")
    return schema, errors, warnings


def main() -> int:
    parser = argparse.ArgumentParser(description="Design the database-schema contract and emit DBML for dbdiagram.io.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--request", required=True, help="text or @path")
    parser.add_argument("--roadmap", help="roadmap.json to derive data requirements from")
    parser.add_argument("--repo", help="repository path; defaults to ~/projects/<slug>")
    parser.add_argument("--out-dir", help="artifact directory; defaults to the state dir")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--slice", metavar="AGGREGATE", help="print one aggregate slice of the contract and exit")
    parser.add_argument(
        "--no-groups", action="store_true", help="omit TableGroup blocks in DBML (a paid dbdiagram.io feature)"
    )
    parser.add_argument("--reemit", help="regenerate artifacts from an existing schema.json without calling the model")
    args = parser.parse_args()

    repo = Path(os.path.expanduser(args.repo or f"~/projects/{args.project}"))
    cfg = project_config(args.project)
    cfg["slug"] = args.project
    out_dir = Path(os.path.expanduser(args.out_dir)) if args.out_dir else STATE / args.project / "schema"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "contract").mkdir(exist_ok=True)

    requirements = roadmap_context(Path(args.roadmap)) if args.roadmap else ""
    if args.slice:
        source = Path(os.path.expanduser(args.reemit or (out_dir / "schema.json")))
        schema = json.loads(source.read_text(encoding="utf-8"))["schema"]
        for agg in schema["aggregates"]:
            if agg["id"] == args.slice:
                print(json.dumps(agg, ensure_ascii=False, indent=1))
                return 0
        print(f"ERROR: no aggregate {args.slice!r}", file=sys.stderr)
        return 1
    if args.reemit:
        payload = json.loads(Path(os.path.expanduser(args.reemit)).read_text(encoding="utf-8"))
        schema = payload["schema"]
        errors = list((payload.get("validation") or {}).get("errors") or [])
        warnings = list((payload.get("validation") or {}).get("warnings") or [])
    else:
        brief = load_request(args.request)
        schema, errors, warnings = generate(cfg, brief, requirements, repo, out_dir, args.workers)
        for warning in warnings:
            print("warn: " + warning, file=sys.stderr)
    stamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    payload = {
        "project": args.project,
        "generated_at": stamp,
        "model": cfg["model"],
        "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
        "schema": schema,
    }

    (out_dir / "schema.json").write_text(json.dumps(payload, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    (out_dir / "schema.dbml").write_text(dbml(schema, groups=not args.no_groups), encoding="utf-8")
    (out_dir / "schema.md").write_text(markdown(schema), encoding="utf-8")
    for agg in schema["aggregates"]:
        (out_dir / "contract" / f"{agg['id']}.json").write_text(
            json.dumps(agg, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
        )

    summary = {
        "project": args.project,
        "artifacts": {
            "json": str(out_dir / "schema.json"),
            "dbml": str(out_dir / "schema.dbml"),
            "markdown": str(out_dir / "schema.md"),
            "contracts": str(out_dir / "contract"),
        },
        "aggregates": len(schema["aggregates"]),
        "tables": sum(len(a.get("tables") or []) for a in schema["aggregates"]),
        "columns": sum(len(t.get("columns") or []) for a in schema["aggregates"] for t in a.get("tables") or []),
        "refs": sum(
            1
            for a in schema["aggregates"]
            for t in a.get("tables") or []
            for c in t.get("columns") or []
            if (c.get("references") or {}).get("table")
        ),
        "migration_order": [m.get("aggregate") for m in schema["migration_order"]],
        "validation": {"ok": not errors, "errors": errors, "warnings": warnings},
    }
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    if errors:
        for error in errors:
            print("ERROR: " + error, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
