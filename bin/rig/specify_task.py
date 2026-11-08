#!/usr/bin/env python3
"""specify_task.py — freeze the specifications of one subtask: tests first, stubs second.

The specifier role of the cascade. It receives one planned subtask, the repository it lands in
and the testing rules; it returns table-driven tests plus the module skeletons the executor will
fill in. Tests are written by a different role than the code (invariant 3), so they are frozen
in a commit of their own before the executor sees the worktree.

CLI:
  specify_task.py --project <slug> --plan FILE --subtask <id> --worktree PATH
                  [--model MODEL] [--python PATH] [--force] [--dry-run]

Output (stdout, one JSON object):
  {"ok": bool, "subtask": id, "files": [...], "truth_table": [...],
   "commit": sha|null, "red_gate": {...}, "usage": {...}}

State:
  ~/.local/state/cascade/<slug>/specs/<subtask>/<ts>.json — every run
  ~/.local/state/cascade/<slug>/task.json                  — the active subtask (files_allowed)

Exit codes: 0 — tests and stubs are frozen and provably red; 1 — nothing to freeze, or the run
failed (bad answer, path outside files_allowed, tests not red).
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .plan_task import (
    CONFIG,
    STATE,
    extract_json,
    load_keys,
    parse_simple_yaml,
    repo_map,
    token_limit,
)
from .rig_conf import gateway

BIN = Path(__file__).resolve().parent

HOUSE_RULES = """
HOUSE RULES (from the project owner, binding for every role):
- Everything that outlives the conversation is written in ENGLISH: code comments, docstrings,
  test names, commit messages, PR text, ADR text.
- Identifiers, paths and technology names stay English; never translate an identifier.
- A comment exists only where the code cannot state its intent.
- Code follows the repository rules printed below, not your habits."""

SYSTEM_PROMPT = """You are the specifier of an automated development pipeline.

Your job: turn ONE planned subtask into (a) frozen executable tests and (b) the module stubs the
executor will fill in, so that the tests fail on the stub and pass on a correct implementation.

Answer with exactly one JSON object and nothing else:

{"truth_table": [{"case": "short name", "given": "inputs as text", "expect": "output or error"}],
 "files": [{"path": "backend/src/app/domain/skills/sp.py", "kind": "stub", "content": "...full file..."},
           {"path": "backend/tests/unit/skills/test_sp.py", "kind": "test", "content": "...full file..."}]}

Hard rules:
- A `kind: "stub"` path comes from the subtask's `files_allowed` list. No other source file may be
  produced.
- A `kind: "test"` path is derived deterministically from a stub: replace the leading
  `<contour>/src/` with `<contour>/tests/unit/` and prefix the module name with `test_`
  (`backend/src/app/domain/skills/sp.py` -> `backend/tests/unit/domain/skills/test_sp.py`).
  One test file per stub module, all stubs covered.
- When a stub file already exists, keep its existing code verbatim (imports may be extended,
  `__all__` extended) and add only the signatures this subtask introduces. Never drop a function
  that already has a real implementation.
- A package `__init__.py` carries the docstring and the re-exports; it needs no
  `NotImplementedError`, every other stub does.
- A stub is a complete module: real imports, type hints, real signatures from
  `contract.new_signatures`, and a body of exactly `raise NotImplementedError`. No partial logic,
  no TODOs, no pass.
- Two different docstrings: the module docstring says what the module is for; every function and
  class carries its own docstring with behaviour, Arguments, Returns and Raises. Never put the
  argument list of a function into the module docstring.
- Tests are pytest, pure: no network, no database, no clock reads, no filesystem writes.
- Test names state the behaviour (`test_rank_1_level_5_is_256000`), never the function name.
- Table-driven where the subtask says so: parametrize over the table, and make the table the
  `truth_table` you return.
- The expected values must be derived from the documented rule, independent of the stub.
- Tests must fail on the stub (`NotImplementedError`) and pass on a correct implementation.
- Cover exactly the acceptance pairs of the subtask, including its error cases.
- Standard library only, Python 3.14, import path `app.*` (the backend puts `src` on sys.path)."""


def project_config(slug: str, role: str = "specifier") -> dict:
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
        "base_url": gateway(project, script="specify_task.py"),
        "temperature": float(params.get("temperature", 0.1)),
        "max_tokens": token_limit(params.get("max_tokens")),
    }


def load_plan(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("plan") or data


def find_subtask(plan: dict, subtask_id: str) -> dict:
    for item in plan.get("subtasks") or []:
        if item.get("id") == subtask_id:
            return item
    raise SystemExit(f"ERROR: no subtask {subtask_id!r} in the plan")


def existing_files(worktree: Path, allowed: list[str]) -> str:
    """Content of the files the specifier may extend, so a stub never erases working code."""
    parts = []
    for rel in allowed:
        path = worktree / rel
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            parts.append(f"--- {rel} (exists, {len(text)} chars) ---\n{text[:6000]}")
    return "\n\n".join(parts)


def build_user_message(subtask: dict, repo_doc: str, test_rules: str, plan_tests: list[str], existing: str = "") -> str:
    hint = "\n".join(f"- {p}" for p in plan_tests) or "- (none declared: derive the path from the stub)"
    return (
        "SUBTASK (authoritative)\n"
        f"{json.dumps(subtask, ensure_ascii=False, indent=1)}\n\n"
        "TEST PATHS THE PLAN DECLARES FOR THIS MODULE (use exactly one of these as the `path` of\n"
        "your test file; derive it from the stub only when the list is empty)\n"
        f"{hint}\n\n"
        "TESTING RULES OF THIS REPOSITORY\n"
        f"{test_rules}\n\n"
        "REPOSITORY MAP AND RULES\n"
        f"{repo_doc}\n"
        + (
            "\nFILES YOU MAY CHANGE, AS THEY ARE NOW (keep every existing line that works; add only\n"
            "what this subtask needs, and keep the module importing cleanly)\n" + existing + "\n"
            if existing
            else ""
        )
    )


def derived_test_path(stub_path: str) -> str:
    """Where the tests of a stub live: the module path mirrored under tests/unit with test_."""
    prefix = ""
    rest = stub_path
    for contour in ("backend/", "frontend/"):
        if rest.startswith(contour):
            prefix, rest = contour, rest[len(contour) :]
            break
    for src_root in ("src/", "app/", "lib/", "pkg/"):
        if rest.startswith(src_root):
            rest = rest[len(src_root) :]
            break
    # the package root of a src-layout project is not part of the mirrored path
    for package_root in ("app/", "lib/", "pkg/"):
        if rest.startswith(package_root):
            rest = rest[len(package_root) :]
            break
    directory, _, name = rest.rpartition("/")
    stem = name[:-3] if name.endswith(".py") else name
    tail = f"{directory}/test_{stem}.py" if directory else f"test_{stem}.py"
    return f"{prefix}tests/unit/{tail}"


def write_files(
    worktree: Path, files: list[dict], allowed: list[str], force: bool, plan_tests: set[str] | None = None
) -> tuple[bool, list[str]]:
    errors: list[str] = []
    normalized = [a.rstrip("/") for a in allowed]
    expected_tests = {derived_test_path(a) for a in allowed if "/src/" in f"/{a}"}
    expected_tests |= set(plan_tests or ())
    for item in files:
        path = (item.get("path") or "").strip()
        kind = item.get("kind")
        content = item.get("content")
        if kind not in ("stub", "test"):
            errors.append(f"{path}: unknown kind {kind!r}")
            continue
        if not path or not isinstance(content, str) or not content.strip():
            errors.append(f"{path}: empty content")
            continue
        inside = any(
            path == a or path.startswith(a.rstrip("/") + "/") or (a.endswith(".py") and path == a) for a in normalized
        )
        if kind == "test":
            stem_ok = any(path.endswith(f"test_{Path(a).stem}.py") for a in allowed)
            under_tests = "/tests/" in f"/{path}" or path.startswith("tests/")
            if path not in expected_tests and not (stem_ok and under_tests):
                errors.append(f"{path}: a test must live at one of {sorted(expected_tests)}")
                continue
            inside = True
        if not inside:
            errors.append(f"{path}: outside files_allowed {allowed}")
            continue
        if kind == "test" and "/tests/" not in f"/{path}":
            errors.append(f"{path}: a test file must live under tests/")
            continue
        if kind == "stub" and "/src/" not in f"/{path}":
            errors.append(f"{path}: a stub must live under src/")
            continue
        extending = (worktree / path).exists()  # an extension keeps real code, it is not a stub
        is_error_module = "Error(" in content and "NotImplementedError" not in content
        if (
            kind == "stub"
            and "NotImplementedError" not in content
            and not path.endswith("__init__.py")
            and not extending
            and not is_error_module
        ):
            errors.append(f"{path}: a stub must raise NotImplementedError")
            continue
        if kind == "test" and "NotImplementedError" in content:
            errors.append(f"{path}: a test must not mention NotImplementedError")
            continue
        target = worktree / path
        if kind == "stub" and target.exists() and not force:
            # The specifier may extend a module that already has real code, never rewrite it.
            old = target.read_text(encoding="utf-8")
            symbols = re.findall(r"(?m)^(?:\s*)(?:def|class)\s+(\w+)", old)
            lost = [s for s in symbols if not re.search(rf"(?m)^\s*(?:def|class)\s+{re.escape(s)}\b", content)]
            if lost:
                errors.append(
                    f"{path}: the answer drops existing definitions {lost}; "
                    "a stub must only add to a module that already has code"
                )
                continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
    for item in files:
        path = (item.get("path") or "").strip()
        target = worktree / path
        if not path.endswith(".py") or not target.exists():
            continue
        compiled = subprocess.run([sys.executable, "-m", "py_compile", str(target)], capture_output=True, text=True)
        if compiled.returncode != 0:
            errors.append(f"{path}: does not compile: {compiled.stderr.strip()[:300]}")
    return not errors, errors


def record_usage(slug: str, feature: str, task_id: str, model: str, usage: dict) -> None:
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cmd = [
        sys.executable,
        str(BIN / "usage.py"),
        "record",
        "-p",
        slug,
        "-f",
        feature,
        "-t",
        task_id,
        "-r",
        "specifier",
        "-m",
        model,
        "--input",
        str(prompt_tokens),
        "--output",
        str(completion_tokens),
        "--note",
        "specify_task.py",
    ]
    if cached:
        cmd += ["--cache-read", str(cached)]
    subprocess.run(cmd, capture_output=True, text=True)


def git(worktree: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Freeze tests and stubs for one subtask.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", required=True, help="plan.json produced by plan_task.py")
    ap.add_argument("--subtask", required=True)
    ap.add_argument("--worktree", required=True, help="git worktree of the task branch")
    ap.add_argument("--model", help="override the specifier model (debug runs use a cheap one)")
    ap.add_argument(
        "--python",
        default=str(Path.home() / "projects" / "occc" / "backend" / ".venv" / "bin" / "python"),
        help="interpreter that can import the backend dependencies",
    )
    ap.add_argument("--feature", default="", help="feature id recorded in the ledger")
    ap.add_argument("--reference", help="file with authoritative values the tests must copy, not derive")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true", help="print the answer, write nothing")
    args = ap.parse_args()

    worktree = Path(args.worktree).expanduser().resolve()
    if not (worktree / ".git").exists():
        raise SystemExit(f"ERROR: {worktree} is not a git worktree")

    plan = load_plan(Path(args.plan))
    subtask = find_subtask(plan, args.subtask)
    allowed = list(subtask.get("files_allowed") or [])
    if not allowed:
        raise SystemExit("ERROR: the subtask has no files_allowed")

    cfg = project_config(args.project)
    if args.model:
        cfg["model"] = args.model

    test_rules_path = worktree / ".agents" / "rules" / "testing.md"
    test_rules = test_rules_path.read_text(encoding="utf-8") if test_rules_path.exists() else ""
    plan_tests = sorted(
        {f for s in plan.get("subtasks") or [] for f in s.get("files_allowed") or [] if "/tests/" in f"/{f}"}
    )
    reference = ""
    if args.reference:
        ref_path = Path(args.reference if not args.reference.startswith("@") else args.reference[1:]).expanduser()
        reference = ref_text = ref_path.read_text(encoding="utf-8")
        if "REFERENCE" not in ref_text.upper():
            reference = "AUTHORITATIVE REFERENCE VALUES\n" + ref_text
    user_message = build_user_message(
        subtask, repo_map(worktree), test_rules, plan_tests, existing_files(worktree, allowed)
    )
    if reference:
        user_message += "\n" + reference + "\n"

    spec_dir_early = STATE / args.project / "specs" / args.subtask
    spec_dir_early.mkdir(parents=True, exist_ok=True)

    from plan_task import call_model, call_model_stream

    raw, usage = call_model_stream(cfg, SYSTEM_PROMPT + HOUSE_RULES, user_message)
    (spec_dir_early / "raw.txt").write_text(raw, encoding="utf-8")
    try:
        answer = extract_json(raw)
    except (SystemExit, ValueError):
        repair = {
            "model": cfg["model"],
            "key": cfg["key"],
            "base_url": cfg["base_url"],
            "temperature": 0.0,
            "max_tokens": cfg["max_tokens"],
        }
        fixed, repair_usage = call_model(
            repair,
            "You repair JSON. The input is a malformed JSON object produced by a model. "
            "Return only the same object, made strictly valid: escape newlines and quotes inside "
            "strings, close every bracket. Do not add, remove or reword any content.",
            raw[:60000],
        )
        (spec_dir_early / "raw.repair.txt").write_text(fixed, encoding="utf-8")
        usage = {
            **usage,
            "prompt_tokens": int(usage.get("prompt_tokens") or 0) + int(repair_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(usage.get("completion_tokens") or 0)
            + int(repair_usage.get("completion_tokens") or 0),
        }
        answer = extract_json(fixed)

    files = answer.get("files") or []

    # every source file the plan allows must exist after the specifier: the executor is gated to it
    expected_stubs = [f for f in allowed if f.endswith(".py")]
    answered = {f.get("path") for f in files if isinstance(f, dict)}
    missing = [f for f in expected_stubs if f not in answered]
    if missing and not args.dry_run:
        retry_message = (
            user_message
            + "\n\nYOUR PREVIOUS ANSWER OMITTED THESE FILES FROM `files_allowed`: "
            + ", ".join(missing)
            + "\nEmit them now, complete, in the same JSON object: stubs with real signatures and "
            "`raise NotImplementedError`, and the test file that imports what they export."
        )
        raw2, usage2 = call_model_stream(cfg, SYSTEM_PROMPT + HOUSE_RULES, retry_message)
        (STATE / args.project / "specs" / args.subtask / "raw.retry.txt").write_text(raw2, encoding="utf-8")
        try:
            answer2 = extract_json(raw2)
        except (SystemExit, ValueError):
            answer2 = {"files": []}
        for item in answer2.get("files") or []:
            if isinstance(item, dict) and item.get("path") in missing:
                files.append(item)
        answer["files"] = files
        for key_name in ("prompt_tokens", "completion_tokens"):
            usage = {**(usage or {})}
            usage[key_name] = int(usage.get(key_name) or 0) + int(usage2.get(key_name) or 0)

    truth_table = answer.get("truth_table") or []
    files = [f for f in files if isinstance(f, dict)]
    test_paths = [f["path"] for f in files if f.get("kind") == "test"]
    if not test_paths:
        print("ERROR: the answer contains no test file", file=sys.stderr)
        return 1

    result = {
        "ok": False,
        "subtask": args.subtask,
        "model": cfg["model"],
        "files": [{"path": f.get("path"), "kind": f.get("kind")} for f in files],
        "truth_table": truth_table,
        "commit": None,
        "red_gate": None,
        "usage": {"prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")},
    }

    spec_dir = STATE / args.project / "specs" / args.subtask
    spec_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    (spec_dir / f"{stamp}.json").write_text(
        json.dumps({**result, "answer": answer}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (spec_dir / "latest.json").write_text(
        json.dumps({**result, "answer": answer}, ensure_ascii=False, indent=1) + "\n", encoding="utf-8"
    )
    (spec_dir / "raw.txt").write_text(raw, encoding="utf-8")

    if args.dry_run:
        result["ok"] = True
        print(json.dumps({**result, "answer": answer}, ensure_ascii=False, indent=1))
        return 0

    ok, errors = write_files(worktree, files, allowed, args.force, set(plan_tests))
    if not ok:
        git(worktree, "checkout", "--", ".")
        git(worktree, "clean", "-fdq")
        print("\n".join(errors), file=sys.stderr)
        record_usage(args.project, args.feature or args.subtask, args.subtask, cfg["model"], usage)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    state = STATE / args.project
    (state / "task.json").write_text(
        json.dumps(
            {
                "id": args.subtask,
                "feature": args.feature,
                "files_allowed": allowed,
                "tests": test_paths,
                "spec_at": stamp,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    git(worktree, "add", "-A")
    commit = git(
        worktree,
        "-c",
        "user.name=cascade-specifier",
        "-c",
        "user.email=cascade@localhost",
        "commit",
        "-m",
        f"test: freeze {args.subtask} specifications",
    )
    if commit.returncode != 0:
        print(f"ERROR: commit failed: {commit.stderr.strip()[:300]}", file=sys.stderr)
        return 1
    sha = git(worktree, "rev-parse", "HEAD").stdout.strip()
    result["commit"] = sha

    backend = worktree / "backend"
    rel_tests = [p.split("backend/", 1)[-1] if p.startswith("backend/") else p for p in test_paths]
    cmd = f"{args.python} -m pytest {' '.join(rel_tests)} -q"
    gate = subprocess.run(
        [sys.executable, str(BIN / "gate.py"), "expect-fail", "--repo", str(backend), "--cmd", cmd],
        capture_output=True,
        text=True,
    )
    result["red_gate"] = json.loads(gate.stdout or "{}")
    result["red_gate"]["command"] = cmd

    record_usage(args.project, args.feature or args.subtask, args.subtask, cfg["model"], usage)
    result["ok"] = bool(result["red_gate"].get("ok"))
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
