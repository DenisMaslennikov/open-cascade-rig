#!/usr/bin/env python3
"""run_role.py — run an intellectual role as an agent with tools, not as a single chat call.

The architect, the specifier, the designer and the chief architect need to read the repository,
write files, run their own checks and fix what they broke. That is an agent loop, so the role is
defined where it belongs: in the system prompt of the conversation, with a tool set and a
deterministic acceptance command the role is told to satisfy before it stops.

CLI:
  run_role.py --project <slug> --role <role> --request "text|@file" --worktree PATH
              [--verify "cmd"] [--allowed a,b] [--out DIR] [--model MODEL]
              [--iterations 60] [--budget 2.0] [--status]

Output (stdout, one JSON object):
  {"ok": bool, "role": ..., "model": ..., "verify": {...}, "commit": sha|null,
   "usage": {...}, "run_dir": path}

State:
  ~/.local/state/cascade/<slug>/roles/<role>/<ts>/{record.json,log.txt,prompt.md}

Exit codes: 0 — the acceptance command passed and the result was committed; 1 — anything else.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from rig_conf import gateway

BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))

from plan_task import CONFIG, STATE, load_keys, parse_simple_yaml, token_limit  # noqa: E402

OH_VENV = Path.home() / "soft" / "openhands" / ".venv"
BIN_PY = OH_VENV / "bin" / "python"
PROMPTS = CONFIG / "prompts"

ROLE_TOOLS = {
    "architect": ["terminal", "file_editor", "glob", "grep"],
    "specifier": ["terminal", "file_editor", "glob", "grep"],
    "designer": ["terminal", "file_editor", "glob", "grep"],
    "chief_architect": ["terminal", "file_editor", "glob", "grep"],
    # Reviewers read the repository and the diff; they never write. No file_editor at all, and the
    # post-run gate rejects the run if the worktree moved anyway (terminal is not read-only by
    # construction, so the guarantee is the gate, not the tool list).
    "reviewer": ["terminal", "glob", "grep"],
    "reviewer_final": ["terminal", "glob", "grep"],
    # A small, well-scoped chore outside the subtask planner (CI config, an import, a rename).
    # Anything that needs a frozen test and a red gate goes through specify_task + run_subtask.
    "executor": ["terminal", "file_editor", "glob", "grep"],
}

READ_ONLY_ROLES = {"reviewer", "reviewer_final"}

ROLE_FALLBACK_PROMPT = {
    "architect": "You are the architect of an automated development pipeline. You explore the "
    "repository, then write a plan of small subtasks as JSON that validates against "
    "the plan schema.",
    "designer": "You are the designer of an automated development pipeline. You write hand-made "
    "HTML artefacts in the design directory of the repository and you check them in a "
    "browser before you stop.",
    "specifier": "You are the specifier of an automated development pipeline. You freeze tests and "
    "stubs for one subtask.",
    "chief_architect": "You are the chief architect: you answer the owner's question about the "
    "architecture in prose, with alternatives and risks.",
    "executor": "You are the executor of an automated development pipeline. You make a small, "
    "explicitly scoped change and run the acceptance command until it passes.",
    "reviewer": "You are the reviewer of an automated development pipeline. You read the diff and "
    "the surrounding code, and you report findings for one small subtask. You never "
    "modify files.",
    "reviewer_final": "You are the final reviewer of an automated development pipeline. You read "
    "the whole pull request against the main branch and the code it touches, and "
    "you report findings once, for the owner. You never modify files.",
}

HOUSE = """

HOUSE RULES (binding):
- Everything that outlives the conversation is written in ENGLISH: code, comments, docstrings,
  test names, commit messages, artefacts.
- The conversation with the owner and the interface strings stay RUSSIAN.
- Work only inside the workspace. Never touch files outside the scope you were given.
- Do not run git commands, do not commit: the orchestrator commits after it accepts your work.
- Stop when the acceptance command passes. Do not gold-plate."""

READ_ONLY_HOUSE = """

HOUSE RULES FOR A READ-ONLY ROLE (binding):
- You never modify the workspace. No file editor, no `git add`, `commit`, `checkout`, `reset`,
  `stash`, `apply`, `restore`, no redirects that write a file. Reading, searching,
  `git diff` / `git log` / `git show` / `git status` and running the test suite are allowed;
  a single modified or created file makes the whole run a failure.
- Your deliverable is the final message, not a file. Structure it exactly:
  `## Findings` (each finding: severity, `path:line`, what is wrong, why it matters),
  `## Verdict` (one of: block / comment / approve, plus one sentence),
  `## Что проверить человеку` — OPTIONAL, in Russian, 3 to 5 bullets, only when a human really
  has to decide something a machine cannot settle (a product decision, a licence or security
  judgement, data loss, a fact from outside the repo). When there is nothing to check, omit the
  section entirely instead of inventing points for the owner to read.
- Prove every finding from files you actually read in this workspace. No finding about code you
  did not open, no advice to «проверить тесты» as a finding.
- Artifacts, code and comments are English; only the «Что проверить человеку» section is Russian."""


def project_config(slug: str, role: str) -> dict:
    project = parse_simple_yaml(CONFIG / "projects" / f"{slug}.yaml")
    roles = parse_simple_yaml(CONFIG / "roles.yaml")
    model = (project.get("models") or {}).get(role, "")
    if not model:
        raise SystemExit(f"ERROR: {role} model is not set for {slug}")
    key_ref = project.get("_", {}).get("key_ref") or "LITELLM_KEY"
    key = load_keys().get(key_ref, "")
    if not key:
        raise SystemExit(f"ERROR: {key_ref} is missing from {CONFIG / 'keys.env'}")
    params = (roles.get("roles") or {}).get(role, {})
    return {
        "model": model,
        "key": key,
        "base_url": gateway(project, script="run_role.py"),
        "temperature": float(params.get("temperature", 0.2)),
        "max_tokens": token_limit(params.get("max_tokens")),
    }


def role_prompt(role: str, verify: str | None) -> str:
    path = PROMPTS / f"{role}.md"
    text = path.read_text(encoding="utf-8") if path.exists() else ROLE_FALLBACK_PROMPT.get(role, "")
    if not text:
        raise SystemExit(f"ERROR: no prompt for role {role!r}")
    text += READ_ONLY_HOUSE if role in READ_ONLY_ROLES else HOUSE
    if verify:
        text += (
            f"\n\nACCEPTANCE COMMAND (run it yourself, in the workspace root, until it passes):\n"
            f"    {verify}\nIt is the definition of done: a red acceptance command means your "
            "work is not finished, whatever you believe about it."
        )
    return text


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True)


def changed_files(worktree: Path, base: str | None = None) -> list[str]:
    """Tracked changes plus untracked files, as repository-relative paths.

    `git status --porcelain` pads its columns and mangles a path with a leading rename marker;
    asking git for the lists directly is both simpler and correct for the scope gate.
    """
    tracked = git(worktree, "diff", "--name-only", "HEAD").stdout.splitlines()
    staged = git(worktree, "diff", "--name-only", "--cached").stdout.splitlines()
    untracked = git(worktree, "ls-files", "--others", "--exclude-standard").stdout.splitlines()
    # a role that resolves a conflict commits by itself; then the tree is clean and only the
    # commits between the starting point and HEAD show the work
    committed = git(worktree, "diff", "--name-only", f"{base}..HEAD").stdout.splitlines() if base else []
    return sorted({p.strip() for p in (*tracked, *staged, *untracked, *committed) if p.strip()})


def metrics_of(conversation) -> dict:
    try:
        snapshot = conversation.conversation_stats.get_combined_metrics()
        data = snapshot.model_dump()
    except Exception:
        return {"prompt_tokens": 0, "completion_tokens": 0, "cost_usd": 0.0}
    usage = data.get("accumulated_token_usage") or {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cost_usd": float(data.get("accumulated_cost") or 0),
    }


def record_usage(slug: str, role: str, model: str, usage: dict) -> None:
    cmd = [
        sys.executable,
        str(BIN / "usage.py"),
        "record",
        "-p",
        slug,
        "-f",
        f"_role:{role}",
        "-t",
        "_role",
        "-r",
        role,
        "-m",
        model,
        "--input",
        str(usage.get("prompt_tokens") or 0),
        "--output",
        str(usage.get("completion_tokens") or 0),
        "--note",
        "run_role.py",
    ]
    if usage.get("cache_read_tokens"):
        cmd += ["--cache-read", str(usage["cache_read_tokens"])]
    subprocess.run(cmd, capture_output=True, text=True)


def gateway_prices(model: str) -> dict[str, float] | None:
    """The gateway's price per token for one model, from its own /model/info cache."""
    cache = CONFIG / "pricing.json"
    if not cache.exists():
        return None
    try:
        entry = (json.loads(cache.read_text(encoding="utf-8")) or {}).get(model)
    except json.JSONDecodeError:
        return None
    if not entry:
        return None
    return {"in": float(entry.get("in") or 0.0), "out": float(entry.get("out") or 0.0)}


def budget_scale(model: str) -> float:
    """How many gateway-currency units one list-price unit buys, for this model.

    The runtime compares `max_budget_per_run` against what *it* thinks the tokens cost, and what it thinks
    comes from the gateway, whose overrides are 30-70x the list price. A budget written in the owner's
    money therefore has to be scaled, or a cents-long run is killed as if it were a dollar-long one: an
    architect run that cost $0.009 of list price died at "accumulated $2.3351 of $2.0000".

    Returns 1.0 when either price is unknown: an unscaled budget is the old behaviour, which is wrong but
    loud, rather than a silently generous cap.
    """
    listed, billed = list_prices(model), gateway_prices(model)
    if not listed or not billed or not listed["in"] or not listed["out"]:
        return 1.0
    return max(billed["in"] / listed["in"], billed["out"] / listed["out"])


def list_prices(model: str) -> dict[str, float] | None:
    """OpenRouter list prices for one model, per token, or None when the cache has no entry.

    The run budget (`max_budget_per_run`) is compared against the prices the runtime knows. When the
    runtime prices a route with the local gateway's overrides — 30-70x the list price — a cheap run
    looks expensive and is killed mid-work: a real architect run that cost $0.009 was aborted at
    "$2.33 of $2.00". Handing the runtime the list price makes the budget mean money.
    """
    cache = STATE / "pricing-openrouter.json"
    if not cache.exists():
        return None
    try:
        models = json.loads(cache.read_text(encoding="utf-8")).get("models") or {}
    except json.JSONDecodeError:
        return None
    candidates = [model, model.removeprefix("openai/"), model.removeprefix("~")]
    for name in candidates:
        entry = models.get(name)
        if entry:
            return {"in": float(entry["in"]) / 1e6, "out": float(entry["out"]) / 1e6}
    return None


def build_agent(cfg: dict, role: str, model: str, system_prompt: str):
    from openhands.sdk import LLM, Agent, Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.glob import GlobTool
    from openhands.tools.grep import GrepTool
    from openhands.tools.terminal import TerminalTool

    registry = {"terminal": TerminalTool, "file_editor": FileEditorTool, "glob": GlobTool, "grep": GrepTool}
    tools = []
    for name in ROLE_TOOLS.get(role, ["terminal", "file_editor"]):
        if name == "browser":
            try:
                from openhands.tools.browser_use import BrowserToolSet  # noqa: F401

                tools.append(Tool(name="browser_navigate"))
                continue
            except Exception:
                print("note: browser tool unavailable, continuing without it", file=sys.stderr)
                continue
        factory = registry.get(name)
        if factory is None:
            continue
        try:
            tools.append(Tool(name=factory.name))
        except Exception as exc:
            print(f"note: tool {name} unavailable: {exc}", file=sys.stderr)

    llm_model = model if model.startswith("openai/") else f"openai/{model}"
    prices = list_prices(model)
    if prices is None:
        # Without a known price the runtime counts the run as free and the budget never trips;
        # say so rather than let a dead cap look like a live one.
        print(
            f"note: no OpenRouter price for {model}; the run budget is not enforced in money — "
            f"run bin/prices_openrouter.py --refresh",
            file=sys.stderr,
        )
    llm = LLM(
        model=llm_model,
        base_url=cfg["base_url"],
        api_key=cfg["key"],
        usage_id=f"cascade-{role}",
        temperature=cfg["temperature"],
        max_output_tokens=cfg["max_tokens"],
        input_cost_per_token=prices["in"] if prices else None,
        output_cost_per_token=prices["out"] if prices else None,
    )
    # system_message_suffix is APPENDED to the built-in prompt, which carries the tool protocol.
    # Passing system_prompt= instead replaces that protocol and the model stops calling tools.
    from openhands.sdk.context.agent_context import AgentContext

    return Agent(
        llm=llm,
        tools=tools,
        agent_context=AgentContext(system_message_suffix=system_prompt),
        system_prompt_kwargs={"cli_mode": True},
    )


def status(slug: str, role: str | None) -> int:
    root = STATE / slug / "roles"
    rows = []
    now = time.time()
    for record in sorted(root.glob("*/*/record.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
        data = json.loads(record.read_text(encoding="utf-8"))
        if role and data.get("role") != role:
            continue
        log = record.parent / "log.txt"
        pid = data.get("pid")
        alive = False
        if pid:
            try:
                os.kill(int(pid), 0)
                alive = True
            except OSError:
                alive = False
        rows.append(
            {
                "role": data.get("role"),
                "state": data.get("state"),
                "alive": alive,
                "started": data.get("started"),
                "model": data.get("model"),
                "last_log_seconds_ago": int(now - log.stat().st_mtime) if log.exists() else None,
                "run_dir": str(record.parent),
            }
        )
    print(json.dumps({"runs": rows}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    if BIN_PY.exists() and Path(sys.executable).resolve() != BIN_PY.resolve():
        os.execv(str(BIN_PY), [str(BIN_PY), str(Path(__file__).resolve()), *sys.argv[1:]])

    ap = argparse.ArgumentParser(description="Run an intellectual role as an agent with tools.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--role", required=True, choices=sorted(ROLE_TOOLS))
    ap.add_argument("--request")
    ap.add_argument("--worktree")
    ap.add_argument("--verify", help="acceptance command the role must make pass")
    ap.add_argument("--allowed", help="comma separated paths the role may change")
    ap.add_argument("--model")
    ap.add_argument("--out", help="extra line for the prompt: where the artefacts go")
    ap.add_argument("--iterations", type=int, default=60)
    ap.add_argument("--budget", type=float, default=2.0)
    ap.add_argument("--timeout", type=int, default=2400)
    ap.add_argument("--status", action="store_true")
    ap.add_argument(
        "--allow-dirty",
        action="store_true",
        help="start even when the worktree has uncommitted or unmerged paths "
        "(resolving a merge conflict is the work itself)",
    )
    ap.add_argument(
        "--read-only",
        action="store_true",
        help="the role must not change the workspace (implied by the reviewer roles); "
        "the report is the final message, saved to --report",
    )
    args = ap.parse_args()

    if args.status:
        return status(args.project, args.role if args.role else None)
    if not args.request or not args.worktree:
        raise SystemExit("ERROR: --request and --worktree are required")

    request = args.request
    if request.startswith("@"):
        request = Path(request[1:]).read_text(encoding="utf-8")
    worktree = Path(args.worktree).expanduser().resolve()
    if not (worktree / ".git").exists():
        raise SystemExit(f"ERROR: {worktree} is not a git worktree")

    cfg = project_config(args.project, args.role)
    model = args.model or cfg["model"]
    read_only = bool(args.read_only) or args.role in READ_ONLY_ROLES
    base = git(worktree, "rev-parse", "HEAD").stdout.strip()
    dirty = git(worktree, "status", "--porcelain").stdout.strip()
    # A conflict-resolution task starts dirty by definition: the unmerged paths are the work.
    if dirty and not args.allow_dirty:
        raise SystemExit(f"ERROR: worktree is not clean:\n{dirty[:400]}")
    if dirty:
        print(f"note: starting on a dirty worktree ({len(dirty.splitlines())} paths)", file=sys.stderr)

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = STATE / args.project / "roles" / args.role / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "log.txt"
    record_path = run_dir / "record.json"
    record = {
        "role": args.role,
        "state": "starting",
        "pid": os.getpid(),
        "started": stamp,
        "base": base,
        "model": model,
    }
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")

    system_prompt = role_prompt(args.role, args.verify)
    prompt = f"TASK FROM THE OWNER\n{request.strip()}\n\nWORKSPACE: {worktree}\n"
    if args.out:
        prompt += f"ARTEFACT DIRECTORY: {args.out}\n"
    if args.allowed:
        prompt += f"FILES YOU MAY CHANGE: {args.allowed}\n"
    (run_dir / "prompt.md").write_text(prompt, encoding="utf-8")
    (run_dir / "system-prompt.md").write_text(system_prompt, encoding="utf-8")

    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    agent = build_agent(cfg, args.role, model, system_prompt)
    from openhands.sdk import Conversation

    scale = budget_scale(model)
    budget = args.budget * scale
    if scale == 1.0:
        print("note: no price pair for this model; the run budget stays in gateway currency", file=sys.stderr)
    else:
        print(
            f"note: run budget {args.budget:.4g} list-prices = {budget:.4g} gateway units "
            f"(gateway prices are {scale:.0f}x list for {model})",
            file=sys.stderr,
        )
    conversation = Conversation(
        agent=agent, workspace=str(worktree), max_iteration_per_run=args.iterations, max_budget_per_run=budget
    )

    def on_timeout(signum, frame):
        raise TimeoutError(f"the role did not finish in {args.timeout}s")

    started = time.time()
    saved = os.dup(1)
    error = None
    with log_path.open("a", encoding="utf-8") as log:
        os.dup2(log.fileno(), 1)
        previous = signal.signal(signal.SIGALRM, on_timeout)
        signal.alarm(args.timeout)
        try:
            conversation.send_message(prompt)
            conversation.run()
            # A model that stops without touching a file has done nothing, whatever it says.
            # A read-only role is the exception: nothing changing is its success condition, so the
            # "you wrote nothing" re-prompt would burn tokens arguing with a correct run.
            for round_number in range(2, 4):
                if read_only or changed_files(worktree, base):
                    break
                conversation.send_message(
                    "You have not changed a single file. The task is not done: the artefacts of this "
                    "role are files in the workspace, and an unchanged workspace means no work was "
                    "done. Do it now — read what you need, then write or edit the files, then run "
                    "the acceptance command. Do not reply with a plan or a summary."
                )
                conversation.run()
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            signal.alarm(0)
            os.dup2(saved, 1)
            os.close(saved)
            signal.signal(signal.SIGALRM, previous)

    usage = metrics_of(conversation)
    usage["seconds"] = round(time.time() - started, 1)
    record_usage(args.project, args.role, model, usage)

    # A reviewer's deliverable is prose, not a diff: take the agent's final message and keep it as
    # a file, so the report outlives the log and can be quoted to the owner.
    report_text = ""
    try:
        from openhands.sdk.conversation import get_agent_final_response

        report_text = get_agent_final_response(conversation.state.events) or ""
    except Exception as exc:
        print(f"note: could not read the final message: {exc}", file=sys.stderr)
    if report_text.strip():
        (run_dir / "report.md").write_text(report_text, encoding="utf-8")

    # Keep the conversation itself, not only stdout: when a role misbehaves the question is always
    # "what did the model answer, and what did it think before that", and the banner-covered log
    # cannot answer it. One JSON object per event, plus a readable transcript.
    try:
        events = list(conversation.state.events)
        with (run_dir / "events.jsonl").open("w", encoding="utf-8") as dump:
            for event in events:
                try:
                    dump.write(event.model_dump_json() + "\n")
                except Exception:
                    dump.write(json.dumps({"type": type(event).__name__, "repr": repr(event)[:2000]}) + "\n")
        lines = []
        for event in events:
            kind = type(event).__name__
            message = getattr(event, "llm_message", None) or getattr(event, "message", None)
            if kind == "MessageEvent" and message is not None:
                text = getattr(message, "content", "")
                if isinstance(text, list):
                    text = " ".join(str(getattr(part, "text", part)) for part in text)
                lines.append(f"[{getattr(event, 'source', '?')}] {text}")
            elif kind == "ActionEvent":
                lines.append(f"[tool] {getattr(event, 'tool_name', '?')}: {str(getattr(event, 'action', ''))[:2000]}")
            elif kind == "ObservationEvent":
                lines.append(f"[obs] {str(getattr(event, 'observation', ''))[:1500]}")
        (run_dir / "transcript.md").write_text("\n\n".join(lines), encoding="utf-8")
    except Exception as exc:
        print(f"note: could not dump the conversation: {exc}", file=sys.stderr)

    result = {
        "ok": False,
        "role": args.role,
        "model": model,
        "commit": None,
        "usage": usage,
        "run_dir": str(run_dir),
        "error": error,
        "report": str(run_dir / "report.md") if report_text.strip() else None,
    }
    if error:
        record.update({"state": "failed", "result": result})
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 1

    changed = changed_files(worktree, base)
    result["changed"] = changed
    if read_only:
        # The read-only guarantee is this gate, not the tool list: terminal is a shell and can
        # write. A reviewer that edits the branch under review has destroyed the evidence.
        if changed or not report_text.strip():
            result["violations"] = changed
            result["error"] = "read-only role changed the worktree" if changed else "read-only role produced no report"
            record.update({"state": "failed", "result": result})
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 1
        result["ok"] = True
        record.update({"state": "done", "result": result})
        record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    if args.allowed:
        allowed = [a.strip() for a in args.allowed.split(",") if a.strip()]
        violations = [
            f for f in result["changed"] if not any(f == a or f.startswith(a.rstrip("/") + "/") for a in allowed)
        ]
        result["violations"] = violations
        if violations:
            result["ok"] = False
            record.update({"state": "out of scope", "result": result})
            record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            print(json.dumps(result, ensure_ascii=False, indent=1))
            return 1

    verify = None
    if args.verify:
        proc = subprocess.run(args.verify, shell=True, cwd=worktree, capture_output=True, text=True, timeout=900)
        verify = {"command": args.verify, "exit": proc.returncode, "tail": (proc.stdout + proc.stderr)[-1500:]}
        result["verify"] = verify

    if changed and (verify is None or verify["exit"] == 0):
        git(worktree, "add", "-A")
        commit = git(
            worktree,
            "-c",
            "user.name=cascade-" + args.role,
            "-c",
            "user.email=cascade@localhost",
            "commit",
            "-m",
            f"{args.role}: {request.strip().splitlines()[0][:80]}",
        )
        if commit.returncode == 0:
            result["commit"] = git(worktree, "rev-parse", "HEAD").stdout.strip()
            result["ok"] = True
        else:
            result["error"] = commit.stderr.strip()[:400]

    record.update({"state": "done" if result["ok"] else "failed", "result": result})
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
