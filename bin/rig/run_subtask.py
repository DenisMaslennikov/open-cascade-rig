#!/usr/bin/env python3
"""run_subtask.py — run the executor role on one frozen subtask, then gate and commit the result.

The executor is OpenHands in a git worktree of the task branch. Tests are already frozen by the
specifier, so the executor may only touch the files of its `files_allowed` list. After the agent
stops, the script runs the deterministic gates and commits only a green, in-scope result.

CLI:
  run_subtask.py --project <slug> --plan FILE --subtask <id> --worktree PATH
                 [--model MODEL] [--python PATH] [--attempts N] [--timeout SECONDS]
  run_subtask.py --project <slug> --status

Output (stdout, one JSON object):
  {"ok": bool, "subtask": id, "commit": sha|null, "gates": {...}, "attempts": [...],
   "usage": {...}, "run_dir": path}

State:
  ~/.local/state/cascade/<slug>/runs/executor/<subtask>/<ts>/record.json + log.txt + prompt.md

Exit codes: 0 — committed and green; 1 — failed (gates, scope, timeout, agent error).
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

from .plan_task import CONFIG, STATE, load_keys, parse_simple_yaml, token_limit
from .rig_conf import gateway

BIN = Path(__file__).resolve().parent

OH_VENV = Path.home() / "soft" / "openhands" / ".venv"
BIN_PY = OH_VENV / "bin" / "python"

SYSTEM_PROMPT = """You are the executor of an automated development pipeline.

You receive one subtask, the frozen tests that define it, and the module stubs to fill. Your job
is to make the frozen tests pass without changing them and without leaving the file list.

Rules, all binding:
- Edit only files from FILES YOU MAY TOUCH. Never create a file outside that list.
- Never edit anything under a `tests/` directory: tests are frozen.
- Keep the public signatures, type hints and docstrings of the stubs; write the bodies.
- Standard library only. No network, no database, no new dependencies.
- Code, docstrings and comments in English; a comment only where the code cannot state intent.
- After each change run the test command shown to you and iterate until it is green.
- Do not run git commands, do not commit, do not touch other branches: the orchestrator commits.
- Stop as soon as the tests are green. Do not add extra features, files or refactors."""


def project_config(slug: str, role: str = "executor") -> dict:
    project_file = CONFIG / "projects" / f"{slug}.yaml"
    if not project_file.exists():
        raise SystemExit(f"ERROR: no project config at {project_file}")
    project = parse_simple_yaml(project_file)
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
        "base_url": gateway(project, script="run_subtask.py"),
        "temperature": float(params.get("temperature", 0.1)),
        "max_tokens": token_limit(params.get("max_tokens")),
        "attempts": int(
            (parse_simple_yaml(CONFIG / "roles.yaml").get("budgets") or {}).get("attempts_per_subtask", 4) or 4
        ),
    }


def find_subtask(plan: dict, subtask_id: str) -> dict:
    for item in plan.get("subtasks") or []:
        if item.get("id") == subtask_id:
            return item
    raise SystemExit(f"ERROR: no subtask {subtask_id!r} in the plan")


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True)


def gate(command: str, **kwargs) -> dict:
    args = [sys.executable, str(BIN / "gate.py"), command]
    for key, value in kwargs.items():
        if value is None:
            continue
        args.append(f"--{key.replace('_', '-')}")
        if value is not True:
            args.append(str(value))
    proc = subprocess.run(args, capture_output=True, text=True)
    try:
        return json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return {"ok": False, "stdout": proc.stdout[-500:], "stderr": proc.stderr[-500:]}


def read_task(slug: str) -> dict:
    path = STATE / slug / "task.json"
    if not path.exists():
        raise SystemExit("ERROR: no active task.json; run specify_task.py first")
    return json.loads(path.read_text(encoding="utf-8"))


def prompt_for(subtask: dict, task: dict, worktree: Path, python: str, feedback: str = "") -> str:
    tests = task.get("tests") or []
    rel_tests = [p.split("backend/", 1)[-1] if p.startswith("backend/") else p for p in tests]
    test_cmd = f"cd backend && {python} -m pytest {' '.join(rel_tests)} -q"
    stubs = [f for f in subtask.get("files_allowed") or [] if "/src/" in f"/{f}"]
    text = (
        "SUBTASK\n"
        f"{json.dumps(subtask, ensure_ascii=False, indent=1)}\n\n"
        f"WORKSPACE: {worktree}\n"
        f"FROZEN TESTS (never edit): {', '.join(tests)}\n"
        f"STUBS TO IMPLEMENT: {', '.join(stubs)}\n"
        f"FILES YOU MAY TOUCH: {', '.join(subtask.get('files_allowed') or [])}\n"
        f"TEST COMMAND (run from the workspace root): {test_cmd}\n"
    )
    if feedback:
        text += f"\nTHE PREVIOUS ATTEMPT DID NOT PASS. Failing output:\n{feedback[-3000:]}\n"
    return text


def metrics_of(conversation) -> dict:
    """Best-effort token accounting from the SDK metrics; zeros when the SDK does not expose it."""
    try:
        snapshot = conversation.conversation_stats.get_combined_metrics()
    except Exception:
        return {"prompt_tokens": 0, "completion_tokens": 0}
    data = {}
    try:
        data = snapshot.model_dump()
    except Exception:
        data = {}
    usage = (data.get("accumulated_token_usage") or {}) if isinstance(data, dict) else {}
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "cache_read_tokens": int(usage.get("cache_read_tokens") or 0),
        "cost_usd": float(data.get("accumulated_cost") or 0) if isinstance(data, dict) else 0.0,
    }


def record_usage(slug: str, feature: str, task_id: str, model: str, usage: dict) -> None:
    cmd = [
        sys.executable,
        str(BIN / "usage.py"),
        "record",
        "-p",
        slug,
        "-f",
        feature or task_id,
        "-t",
        task_id,
        "-r",
        "executor",
        "-m",
        model,
        "--input",
        str(usage.get("prompt_tokens") or 0),
        "--output",
        str(usage.get("completion_tokens") or 0),
        "--note",
        "run_subtask.py",
    ]
    if usage.get("cache_read_tokens"):
        cmd += ["--cache-read", str(usage["cache_read_tokens"])]
    subprocess.run(cmd, capture_output=True, text=True)


def run_agent(
    system: str, prompt: str, worktree: Path, cfg: dict, model: str, log_path: Path, timeout: int, python: str
) -> dict:
    """One OpenHands conversation against the worktree; returns metrics or raises."""
    os.environ.setdefault("OPENHANDS_SUPPRESS_BANNER", "1")
    from openhands.sdk import LLM, Agent, Conversation, Tool
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool

    llm_model = model if model.startswith("openai/") else f"openai/{model}"
    llm = LLM(
        model=llm_model,
        base_url=cfg["base_url"],
        api_key=cfg["key"],
        usage_id="cascade-executor",
        temperature=cfg["temperature"],
        max_output_tokens=cfg["max_tokens"],
    )
    agent = Agent(
        llm=llm,
        tools=[Tool(name=TerminalTool.name), Tool(name=FileEditorTool.name)],
        system_prompt_kwargs={"cli_mode": True},
    )
    conversation = Conversation(agent=agent, workspace=str(worktree), max_iteration_per_run=80, max_budget_per_run=2.0)

    def on_timeout(signum, frame):
        raise TimeoutError(f"executor did not finish in {timeout}s")

    previous = signal.signal(signal.SIGALRM, on_timeout)
    started = time.time()
    # the SDK prints its own banner and tool chatter on stdout; the JSON answer must stay clean
    saved_stdout = os.dup(1)
    with log_path.open("a", encoding="utf-8") as log:
        os.dup2(log.fileno(), 1)
        signal.alarm(timeout)
        try:
            conversation.send_message(prompt)
            conversation.run()
        finally:
            signal.alarm(0)
            os.dup2(saved_stdout, 1)
            os.close(saved_stdout)
            signal.signal(signal.SIGALRM, previous)
    metrics = metrics_of(conversation)
    metrics["seconds"] = round(time.time() - started, 1)
    with log_path.open("a", encoding="utf-8") as log:
        log.write(f"\n=== attempt finished in {metrics['seconds']}s; usage {metrics} ===\n")
    return metrics


def status(slug: str) -> int:
    root = STATE / slug / "runs" / "executor"
    now = time.time()
    rows = []
    for record_path in sorted(root.glob("*/*/record.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
        data = json.loads(record_path.read_text(encoding="utf-8"))
        log_path = record_path.parent / "log.txt"
        age_log = int(now - log_path.stat().st_mtime) if log_path.exists() else None
        pid = data.get("pid")
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        rows.append(
            {
                "subtask": data.get("subtask"),
                "state": data.get("state"),
                "alive": alive,
                "started": data.get("started"),
                "last_log_seconds_ago": age_log,
                "run_dir": str(record_path.parent),
            }
        )
    print(json.dumps({"runs": rows}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    # the SDK lives in its own interpreter (3.12); re-exec so the caller never has to know
    if BIN_PY.exists() and Path(sys.executable).resolve() != BIN_PY.resolve():
        # Re-exec as a module, never by file path: the child needs `rig` as its parent package or
        # every relative import fails. `bin` goes on the child's PYTHONPATH so any cwd works.
        pythonpath = os.pathsep.join(filter(None, (str(BIN), os.environ.get("PYTHONPATH"))))
        argv = [str(BIN_PY), "-m", "rig.run_subtask", *sys.argv[1:]]
        os.execve(str(BIN_PY), argv, {**os.environ, "PYTHONPATH": pythonpath})
    ap = argparse.ArgumentParser(description="Run the executor on one subtask and gate it.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan")
    ap.add_argument("--subtask")
    ap.add_argument("--worktree")
    ap.add_argument("--model", help="override the executor model")
    ap.add_argument("--python", default=str(Path.home() / "projects" / "occc" / "backend" / ".venv" / "bin" / "python"))
    ap.add_argument("--attempts", type=int, default=0, help="override budgets.attempts_per_subtask")
    ap.add_argument("--timeout", type=int, default=1500, help="wall clock per attempt, seconds")
    ap.add_argument("--feature", default="")
    ap.add_argument("--feedback", help="review findings to fix on this branch (review-fix pass)")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status(args.project)
    for required in ("plan", "subtask", "worktree"):
        if not getattr(args, required):
            raise SystemExit(f"ERROR: --{required} is required")

    worktree = Path(args.worktree).expanduser().resolve()
    plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    plan = plan.get("plan") or plan
    subtask = find_subtask(plan, args.subtask)
    task = read_task(args.project)
    allowed = list(subtask.get("files_allowed") or [])
    if not allowed:
        raise SystemExit("ERROR: the subtask has no files_allowed")

    cfg = project_config(args.project)
    model = args.model or cfg["model"]
    attempts = args.attempts or cfg["attempts"]

    base = git(worktree, "rev-parse", "HEAD").stdout.strip()
    dirty = git(worktree, "status", "--porcelain").stdout.strip()
    if dirty:
        raise SystemExit(f"ERROR: worktree is not clean:\n{dirty[:500]}")
    feedback_seed = ""
    if args.feedback:
        feedback_seed = (
            "REVIEW FINDINGS TO FIX (tests stay frozen; change only files in scope):\n" + args.feedback.strip() + "\n"
        )

    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = STATE / args.project / "runs" / "executor" / args.subtask / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    log_path = run_dir / "log.txt"
    record_path = run_dir / "record.json"
    record_path.write_text(
        json.dumps(
            {
                "subtask": args.subtask,
                "state": "starting",
                "pid": os.getpid(),
                "started": stamp,
                "base": base,
                "model": model,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )

    feedback = feedback_seed
    attempt_rows = []
    total_usage = {"prompt_tokens": 0, "completion_tokens": 0, "cache_read_tokens": 0}
    green = False

    for number in range(1, attempts + 1):
        prompt = prompt_for(subtask, task, worktree, args.python, feedback)
        (run_dir / f"prompt-{number}.md").write_text(prompt, encoding="utf-8")
        record_path.write_text(
            json.dumps(
                {
                    "subtask": args.subtask,
                    "state": f"attempt {number}/{attempts}",
                    "pid": os.getpid(),
                    "started": stamp,
                    "base": base,
                    "model": model,
                },
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        try:
            metrics = run_agent(SYSTEM_PROMPT, prompt, worktree, cfg, model, log_path, args.timeout, args.python)
        except TimeoutError as exc:
            attempt_rows.append({"attempt": number, "error": str(exc)})
            break
        except Exception as exc:
            attempt_rows.append({"attempt": number, "error": f"{type(exc).__name__}: {exc}"})
            break
        for key in total_usage:
            total_usage[key] += int(metrics.get(key) or 0)

        scope = gate("check-diff", repo=str(worktree), allowed=",".join(allowed))
        frozen = gate("frozen-tests", repo=str(worktree), base=base, tests_prefix="backend/tests/")
        rel_tests = [p.split("backend/", 1)[-1] if p.startswith("backend/") else p for p in (task.get("tests") or [])]
        cmd = f"{args.python} -m pytest {' '.join(rel_tests)} -q"
        passing = gate("expect-pass", repo=str(worktree / "backend"), cmd=cmd)
        attempt_rows.append(
            {
                "attempt": number,
                "usage": metrics,
                "check_diff": scope["ok"],
                "frozen_tests": frozen["ok"],
                "expect_pass": passing["ok"],
            }
        )
        if scope.get("ok") and frozen.get("ok") and passing.get("ok"):
            green = True
            break
        feedback = "\n".join(
            filter(
                None,
                [
                    "" if scope.get("ok") else f"files outside files_allowed: {scope.get('violations')}",
                    "" if frozen.get("ok") else f"tests were modified: {frozen.get('violations')}",
                    "" if passing.get("ok") else f"tests still failing:\n{passing.get('tail', '')}",
                ],
            )
        )
        if not scope.get("ok"):
            break

    usage = {**total_usage, "attempts": len(attempt_rows)}
    result = {
        "ok": False,
        "subtask": args.subtask,
        "model": model,
        "commit": None,
        "attempts": attempt_rows,
        "usage": usage,
        "run_dir": str(run_dir),
    }

    if green:
        git(worktree, "add", "-A")
        prefix = "fix" if args.feedback else "feat"
        message = f"{prefix}: {subtask.get('title') or args.subtask}\n\n{subtask.get('goal') or ''}".strip()
        commit = git(
            worktree, "-c", "user.name=cascade-executor", "-c", "user.email=cascade@localhost", "commit", "-m", message
        )
        if commit.returncode == 0:
            result["commit"] = git(worktree, "rev-parse", "HEAD").stdout.strip()
            result["ok"] = True
        else:
            result["error"] = commit.stderr.strip()[:400]

    full = gate("expect-pass", repo=str(worktree / "backend"), cmd=f"{args.python} -m pytest -q")
    result["full_suite"] = {"ok": full.get("ok"), "tail": full.get("tail")}
    record_usage(args.project, args.feature or args.subtask, args.subtask, model, total_usage)

    record_path.write_text(
        json.dumps(
            {
                "subtask": args.subtask,
                "state": "done" if result["ok"] else "failed",
                "pid": os.getpid(),
                "started": stamp,
                "base": base,
                "model": model,
                "result": result,
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
