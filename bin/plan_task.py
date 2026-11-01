#!/usr/bin/env python3
"""plan_task.py — turn a customer request into a validated task plan.

The architect role of the cascade: it receives what the customer wants, a map of the
repository, and the plan schema; it returns either a plan or the questions that block one.

CLI:
  plan_task.py --project <slug> --request "text" [--repo PATH] [--out FILE]
  plan_task.py --project <slug> --request @request.md [--repo PATH]

Input:
  --project    project slug; its config lives in ~/.config/cascade/projects/<slug>.yaml
  --request    the customer's own words, or @path to read them from a file
  --repo       repository to map; defaults to ~/projects/<slug>

Output (stdout, one JSON object):
  {"status": "ready" | "needs_clarification" | "invalid_plan",
   "questions": [...], "assumptions": [...], "plan": {...} | null,
   "validation": {"ok": bool, "errors": [...]}, "usage": {...}}

State:
  ~/.local/state/cascade/<slug>/plans/<timestamp>.json — every run
  ~/.local/state/cascade/<slug>/plan.json             — the latest answer

Exit codes:
  0 — an answer was produced (a valid plan, or the questions that unblock one)
  1 — the run failed (bad input, gateway error, unparseable answer, invalid plan)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CONFIG = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cascade"
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cascade"
GATEWAY = "http://litellm.service/v1"
BIN = Path(__file__).resolve().parent

SKIP_DIRS = {
    ".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "coverage",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "data", ".idea", ".vscode",
    "playwright-report", "test-results", ".npm", ".cache", "site-packages",
}
SKIP_SUFFIXES = {".pyc", ".pyo", ".lock", ".zip", ".png", ".jpg", ".jpeg", ".svg", ".ico", ".woff", ".woff2"}
MAX_MAP_ENTRIES = 500
MAX_DOC_CHARS = 6000
MAX_RULE_CHARS = 3000
MAX_REQUEST_CHARS = 20000


# ---------------------------------------------------------------- configuration
def parse_simple_yaml(path: Path) -> dict[str, dict]:
    """Read `key: value`, one level of nesting, and one more level for named groups.

    That last level is what `roles:` needs: `roles: -> architect: -> max_tokens: 12000`. Without it
    the per-role settings are silently dropped and every role runs on the script's default — a limit
    that appears in the config and does nothing is worse than no config at all.
    """
    out: dict[str, dict] = {"_": {}}
    section = "_"
    group = None
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        key, _, value = raw.strip().partition(":")
        key, value = key.strip(), _clean(value)
        if indent == 0:
            section, group = (key, None) if not value else (section, None)
            if not value:
                out.setdefault(section, {})
            else:
                out[section][key] = value
            continue
        if indent == 2:
            if value:
                out.setdefault(section, {})[key] = value
                group = None
            else:
                out.setdefault(section, {}).setdefault(key, {})
                group = key
            continue
        if group is not None:
            out.setdefault(section, {}).setdefault(group, {})[key] = value
    return out


def _clean(raw: str) -> str:
    return re.sub(r"\s+#.*$", "", raw).strip().strip('"').strip("'")


def token_limit(value) -> int | None:
    """Output ceiling for a role: a number from the config, or `None` when nobody capped it.

    The owner's rule is "no limit unless the config says so": an empty value, `null`, `none` or `0`
    all mean "let the model use its own ceiling". Capping inside the pipeline is how a reasoning
    model ends up returning `content: null` with the whole budget spent on thinking.
    """
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in ("", "null", "none", "unlimited", "0"):
        return None
    return int(text)


def load_keys() -> dict[str, str]:
    """Read KEY=VALUE pairs from the pipeline key store."""
    path = CONFIG / "keys.env"
    keys: dict[str, str] = {}
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                name, value = line.split("=", 1)
                keys[name.strip()] = value.strip()
    return keys


def project_config(slug: str) -> dict:
    """Collect what the architect needs: model, key, sampling parameters."""
    project_file = CONFIG / "projects" / f"{slug}.yaml"
    if not project_file.exists():
        raise SystemExit(f"ERROR: no project config at {project_file}")
    project = parse_simple_yaml(project_file)
    roles = parse_simple_yaml(CONFIG / "roles.yaml")
    models = project.get("models", {})
    model = models.get("architect", "")
    if not model:
        raise SystemExit(f"ERROR: architect model is not set for {slug} (check_project.py reports it)")

    key_ref = project.get("_", {}).get("key_ref") or "LITELLM_KEY"
    key = load_keys().get(key_ref, "")
    if not key:
        raise SystemExit(f"ERROR: {key_ref} is missing from {CONFIG / 'keys.env'}")

    params = (roles.get("roles") or {}).get("architect", {})
    base_url = project.get("_", {}).get("base_url") or GATEWAY
    return {
        "model": model,
        "key": key,
        "key_ref": key_ref,
        "base_url": base_url.rstrip("/"),
        "temperature": float(params.get("temperature", 0.2)),
        "max_tokens": token_limit(params.get("max_tokens")),
        "max_input_tokens": int(params["max_input_tokens"]) if params.get("max_input_tokens") else None,
    }


# ---------------------------------------------------------------- repository map
def repo_map(repo: Path) -> str:
    """Build a compact, deterministic picture of the repository.

    Only paths, sizes and the documents that carry conventions: the architect needs the
    shape of the tree and the rules, not the source.
    """
    if not repo.is_dir():
        raise SystemExit(f"ERROR: not a directory: {repo}")

    files: list[tuple[str, int]] = []
    for root, dirs, names in os.walk(repo):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith(".git"))
        for name in sorted(names):
            path = Path(root) / name
            if path.suffix in SKIP_SUFFIXES or name in (".env",):
                continue
            rel = path.relative_to(repo).as_posix()
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > 400_000:
                continue
            files.append((rel, size))
            if len(files) >= MAX_MAP_ENTRIES:
                break
        if len(files) >= MAX_MAP_ENTRIES:
            break

    lines = [f"REPOSITORY {repo.name}: {len(files)} files (truncated at {MAX_MAP_ENTRIES})", ""]
    for rel, size in files:
        lines.append(f"{rel} ({size} B)")

    for doc in ("AGENTS.md", "README.md"):
        path = repo / doc
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")[:MAX_DOC_CHARS]
            lines += ["", f"--- {doc} ---", text]

    rules_dir = repo / ".agents" / "rules"
    if rules_dir.is_dir():
        lines += ["", "--- PROJECT RULES (.agents/rules) ---"]
        for rule in sorted(rules_dir.glob("*.md")):
            text = rule.read_text(encoding="utf-8", errors="replace")[:MAX_RULE_CHARS]
            lines += [f"### {rule.name}", text]

    adr_dir = repo / "docs" / "adr"
    if adr_dir.is_dir():
        lines += ["", "--- DECISIONS (docs/adr) ---"]
        for adr in sorted(adr_dir.glob("*.md")):
            head = adr.read_text(encoding="utf-8", errors="replace")[:1200]
            title = next(
                (ln.lstrip("# ").strip() for ln in head.splitlines() if ln.startswith("# ADR")),
                adr.stem,
            )
            lines.append(f"{adr.name}: {title}")

    return "\n".join(lines)


# ---------------------------------------------------------------- prompt
HOUSE_RULES = """

HOUSE RULES (from the project owner, binding for every role):
- Everything that outlives the conversation is written in ENGLISH: code comments, docstrings,
  migration and commit messages, branch and PR titles and descriptions, ADR text, rules files,
  table and column notes, schema descriptions, test names.
- The conversation with the owner, board cards and anything he reads as a product artefact stay in
  RUSSIAN. This rule changes the language of the artefacts, not of the dialogue.
- Identifiers, paths, table names, scopes and technology names are English and follow the
  repository conventions; never translate an identifier.
- A comment exists only where the code cannot state its intent. A note exists only where the column
  name is not enough."""


SYSTEM_PROMPT = """You are the architect of an automated development pipeline.

Your job: turn a customer request into a plan of small, independently implementable
subtasks, or — when the request cannot be planned yet — into the questions that unblock it.

Answer with exactly one JSON object and nothing else. Two shapes are allowed.

1. A plan:
{"status": "ready", "assumptions": ["..."], "plan": {"task": {"id", "title", "goal",
 "acceptance_summary"}, "subtasks": [{"id", "title", "goal", "files_allowed",
 "contract": {"new_signatures": [], "breaks": []}, "acceptance": [{"in", "out"}],
 "depends_on": [], "test_command"}]}}

2. Questions, when the request is ambiguous, contradictory or missing information that only
the customer can supply:
{"status": "needs_clarification", "questions": ["..."], "assumptions": ["..."]}

Hard rules for a plan:
- 1..12 subtasks; each fits one pull request and one sitting.
- `files_allowed` lists 1..6 repository-relative paths that must match
  ^((backend|frontend)/)?(src|tests|app|lib|pkg|tools|docs|scripts)/ — use real paths from the map.
- A subtask touches either tests or source files, never both: tests are written by one role,
  code by another.
- Subtasks that are not chained by `depends_on` must not share files: they run in parallel.
- `acceptance` states observable input → output pairs, not implementation steps.
- `depends_on` refers to ids of subtasks that must land first; the graph must be acyclic.
- `test_command` is the command that proves the subtask, run from the repository root.
- Ids are lowercase slugs: task id 3..41 chars, subtask id 2..31 chars.
- The plan must be implementable in the repository as it is; do not assume files that do not
  exist, and say in `assumptions` what you inferred from the customer's words.

The repository map ends with the project's own rules. They are binding: if a rule and your
habit disagree (where models live, where SQL lives, where a component belongs), the rule wins
and the plan must use the paths the rules describe. Subtasks that create a file list it as a
new path in `files_allowed` — that is expected, not an error.

Ask questions instead of guessing when: a product decision is open (who sees what, what
counts as done), a term is ambiguous, a required external credential or permission is
unstated, or two requirements contradict each other. At most 5 questions, each answerable in
one sentence. Do not ask about things the repository already answers.""" + HOUSE_RULES


def build_user_message(request: str, repo_doc: str, schema_text: str) -> str:
    return (
        "CUSTOMER REQUEST\n"
        f"{request.strip()}\n\n"
        "PLAN SCHEMA (authoritative; the answer is rejected if it does not validate)\n"
        f"{schema_text}\n\n"
        "REPOSITORY MAP\n"
        f"{repo_doc}\n"
    )


# ---------------------------------------------------------------- model call
def call_model(cfg: dict, system: str, user: str) -> tuple[str, dict]:
    """Single chat completion against the gateway; returns content and usage."""
    body = {
        "model": cfg["model"],
        "temperature": cfg["temperature"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if cfg.get("max_tokens"):
        body["max_tokens"] = cfg["max_tokens"]
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=600) as response:
            data = json.load(response)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"ERROR: gateway answered {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: gateway unreachable: {exc.reason}") from exc

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise SystemExit(f"ERROR: unexpected gateway payload: {json.dumps(data)[:500]}") from exc
    return content, data.get("usage") or {}


def call_model_stream(cfg: dict, system: str, user: str,
                      idle_timeout: int = 300) -> tuple[str, dict]:
    """Same call, but streamed: a long generation cannot die on a single blocking read.

    The gateway answers with server-sent events; usage arrives in the last chunk when
    `stream_options.include_usage` is set. Timeout is per read, not per answer.
    """
    body = {
        "model": cfg["model"],
        "temperature": cfg["temperature"],
        "stream": True,
        "stream_options": {"include_usage": True},
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    }
    if cfg.get("max_tokens"):
        body["max_tokens"] = cfg["max_tokens"]
    req = urllib.request.Request(
        cfg["base_url"] + "/chat/completions",
        data=json.dumps(body).encode(),
        headers={"Authorization": "Bearer " + cfg["key"], "Content-Type": "application/json",
                 "Accept": "text/event-stream"},
    )
    chunks: list[str] = []
    usage: dict = {}
    try:
        with urllib.request.urlopen(req, timeout=idle_timeout) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    event = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                if event.get("usage"):
                    usage = event["usage"]
                for choice in event.get("choices") or []:
                    if choice.get("finish_reason"):
                        usage["_finish_reason"] = choice["finish_reason"]
                    delta = choice.get("delta") or {}
                    if delta.get("content"):
                        chunks.append(delta["content"])
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise SystemExit(f"ERROR: gateway answered {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"ERROR: gateway unreachable: {exc.reason}") from exc
    except TimeoutError as exc:
        raise SystemExit("ERROR: stream stalled; no chunk for "
                         f"{idle_timeout}s") from exc

    content = "".join(chunks)
    if not content.strip():
        raise SystemExit("ERROR: the stream produced no content")
    return content, usage


def extract_json(text: str) -> dict:
    """Pull the first complete JSON object out of a model answer.

    An empty answer is not "no JSON in the text": a reasoning model that spent its whole output
    budget on reasoning_tokens returns `content: null`, and a caller that only re-raises
    AttributeError on `.strip()` sends the next person hunting for a broken key or gateway.
    """
    if not text or not text.strip():
        raise SystemExit(
            "ERROR: модель вернула пустой content. У reasoning-моделей весь бюджет выхода ушёл "
            "в reasoning_tokens — поднимать max_tokens роли или брать не-reasoning модель; "
            "ключ и шлюз тут ни при чём.")
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    if start < 0:
        raise SystemExit(f"ERROR: no JSON object in the answer: {text[:300]}")
    depth, in_string, escaped = 0, False, False
    for index in range(start, len(cleaned)):
        char = cleaned[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return json.loads(cleaned[start : index + 1])
    raise SystemExit("ERROR: unterminated JSON object in the answer")


# ---------------------------------------------------------------- validation and accounting
def validate_plan(plan: dict, repo: Path) -> tuple[bool, list[str]]:
    """Run the deterministic validator on the plan."""
    tmp = STATE / "_incoming_plan.json"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    tmp.write_text(json.dumps(plan, indent=1), encoding="utf-8")
    proc = subprocess.run(
        [sys.executable, str(BIN / "validate_plan.py"), str(tmp), "--repo", str(repo)],
        capture_output=True,
        text=True,
    )
    notes = [ln for ln in (proc.stderr or "").splitlines() if ln.startswith("note:")]
    errors = [
        ln for ln in (proc.stderr or "").splitlines()
        if ln.strip() and not ln.startswith("note:")
    ]
    for note in notes:
        print(note, file=sys.stderr)
    return proc.returncode == 0, errors


def record_usage(slug: str, model: str, usage: dict, task_id: str) -> None:
    """Append the call to the token ledger; a task without recorded cost is not closed."""
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cmd = [
        sys.executable, str(BIN / "usage.py"), "record",
        "-p", slug, "-f", "_planning", "-t", task_id, "-r", "architect",
        "-m", model, "--input", str(prompt_tokens), "--output", str(completion_tokens),
        "--note", "plan_task.py",
    ]
    if cached:
        cmd += ["--cache-read", str(cached)]
    subprocess.run(cmd, capture_output=True, text=True)


# ---------------------------------------------------------------- main
def main() -> int:
    parser = argparse.ArgumentParser(description="Turn a customer request into a task plan.")
    parser.add_argument("--project", required=True, help="project slug")
    parser.add_argument("--request", required=True, help="customer request, or @path to a file")
    parser.add_argument("--repo", help="repository path; defaults to ~/projects/<slug>")
    parser.add_argument("--out", help="also write the answer to this file")
    parser.add_argument("--model", help="override the architect model (debug on a cheap one)")
    args = parser.parse_args()

    request = args.request
    if request.startswith("@"):
        request = Path(request[1:]).read_text(encoding="utf-8")
    request = request.strip()[:MAX_REQUEST_CHARS]
    if len(request) < 10:
        print("ERROR: the request is too short to plan", file=sys.stderr)
        return 1

    repo = Path(os.path.expanduser(args.repo or f"~/projects/{args.project}"))
    cfg = project_config(args.project)
    if args.model:
        cfg["model"] = args.model
    schema_path = CONFIG / "schema" / "plan.schema.json"
    schema_text = schema_path.read_text(encoding="utf-8")

    user_message = build_user_message(request, repo_map(repo), schema_text)
    if cfg["max_input_tokens"]:
        approx_tokens = len(user_message) // 4
        if approx_tokens > cfg["max_input_tokens"]:
            print(
                f"ERROR: input is ~{approx_tokens} tokens, above the {cfg['max_input_tokens']} "
                "limit for this role; shorten the request or the repository map",
                file=sys.stderr,
            )
            return 1

    # A plan is a long generation: the single blocking POST dies on a read timeout and a broken
    # stream shows up as "unterminated JSON" instead of what it is. Stream with an idle timeout,
    # and treat a `length` finish as truncation rather than handing it to the validator.
    content, usage = call_model_stream(cfg, SYSTEM_PROMPT, user_message, idle_timeout=600)
    if usage.get("_finish_reason") == "length":
        raw_dir = STATE / args.project / "plans" / "raw"
        raw_dir.mkdir(parents=True, exist_ok=True)
        raw_path = raw_dir / f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.txt"
        raw_path.write_text(content, encoding="utf-8")
        print(f"ERROR: ответ обрезан по max_tokens (finish_reason=length); сырой ответ {raw_path}. "
              f"Поднять max_tokens роли architect или сузить запрос.", file=sys.stderr)
        return 1
    answer = extract_json(content)

    status = answer.get("status")
    if status not in ("ready", "needs_clarification"):
        print(f"ERROR: unknown status {status!r} in the answer", file=sys.stderr)
        return 1

    result: dict = {
        "status": status,
        "questions": answer.get("questions") or [],
        "assumptions": answer.get("assumptions") or [],
        "plan": None,
        "validation": {"ok": False, "errors": []},
        "usage": {
            "model": cfg["model"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        },
    }

    exit_code = 0
    if status == "ready":
        plan = answer.get("plan") or {}
        ok, errors = validate_plan(plan, repo)
        result["plan"] = plan
        result["validation"] = {"ok": ok, "errors": errors}
        if not ok:
            result["status"] = "invalid_plan"
            exit_code = 1
            print("\n".join(errors), file=sys.stderr)
            plan_id = "_invalid"
        else:
            plan_id = (plan.get("task") or {}).get("id") or "_plan"
        record_usage(args.project, cfg["model"], usage, plan_id)
    else:
        if not result["questions"]:
            print("ERROR: needs_clarification without questions", file=sys.stderr)
            return 1
        record_usage(args.project, cfg["model"], usage, "_clarification")

    state_dir = STATE / args.project
    (state_dir / "plans").mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    payload = json.dumps(result, ensure_ascii=False, indent=1)
    (state_dir / "plans" / f"{stamp}.json").write_text(payload + "\n", encoding="utf-8")
    (state_dir / "plan.json").write_text(payload + "\n", encoding="utf-8")
    if args.out:
        Path(args.out).write_text(payload + "\n", encoding="utf-8")

    print(payload)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
