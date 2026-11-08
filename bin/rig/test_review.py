#!/usr/bin/env python3
"""test_review.py — ask whether the frozen tests can be satisfied before a run is spent.

The step between the specifier and the executor: one subtask's frozen tests go to a model that
has not seen any implementation (there is none yet), together with the plan's logic description
and acceptance pairs. The model answers whether a correct implementation of the description can
make every test pass, and reports what it cannot derive. A test that asserts the stub, a
property that no implementation of the description can satisfy, or an acceptance pair the
description contradicts costs the executor a whole run; this call catches it first.

CLI:
  test_review.py --project <slug> --plan FILE --subtask <id> --worktree PATH
                 [--gate-output FILE] [--out FILE] [--json]

Output (stdout, one JSON object):
  {"verdict": "pass"|"fail", "who_next": "executor"|"specifier"|"architect",
   "findings": [...], "model": ..., "usage": {...}}

Exit codes:
  0 — pass (the executor may start)
  1 — fail (the findings return to the specifier, a plan inconsistency to the architect)
  2 — usage error or an unparseable answer (the raw answer goes to stderr)
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from .plan_task import CONFIG, call_model, load_keys, parse_simple_yaml, token_limit
from .rig_conf import gateway

PKG_ROOT = Path(__file__).resolve().parent.parent

VERDICT_KEYS = ("verdict", "findings")
FINDING_KEYS = ("kind", "severity", "test", "witness", "reason")
SEVERITIES = ("blocker", "major", "minor")
FINDING_KINDS = ("contradicts_spec", "unsatisfiable", "underspecified", "plan_inconsistent", "asserts_stub")


def system_prompt() -> str:
    """The step's system prompt, read from its file; never inlined here."""
    return (CONFIG / "prompts" / "test_review.md").read_text(encoding="utf-8")


def project_config(slug: str) -> dict:
    """Model, key and gateway for the `test_reviewer` role; no default and no fallback."""
    project = parse_simple_yaml(CONFIG / "projects" / f"{slug}.yaml")
    roles = parse_simple_yaml(CONFIG / "roles.yaml")
    model = (project.get("models") or {}).get("test_reviewer", "")
    if not model:
        raise SystemExit(f"ERROR: test_reviewer model is not set for {slug}")
    key_ref = project.get("_", {}).get("key_ref") or "LITELLM_KEY"
    key = load_keys().get(key_ref, "")
    if not key:
        raise SystemExit(f"ERROR: {key_ref} is missing from {CONFIG / 'keys.env'}")
    params = (roles.get("roles") or {}).get("test_reviewer", {})
    return {
        "model": model,
        "key": key,
        "base_url": gateway(project, script="test_review.py").rstrip("/"),
        "temperature": float(params.get("temperature", 0.0)),
        "max_tokens": token_limit(params.get("max_tokens")),
    }


# ---------------------------------------------------------------- the pure three
def _plan_part(plan: dict) -> dict:
    """The `plan` object of the artifact: `plan_task.py` wraps it, a bare plan also works."""
    return plan.get("plan") or plan


def _subtask(plan: dict, subtask_id: str) -> dict:
    plan_part = _plan_part(plan)
    ids = [str(s.get("id") or "") for s in plan_part.get("subtasks") or []]
    for subtask in plan_part.get("subtasks") or []:
        if subtask.get("id") == subtask_id:
            return subtask
    raise SystemExit(f"ERROR: no subtask {subtask_id!r} in the plan; the plan carries: {', '.join(ids)}")


def build_user(plan: dict, subtask_id: str, stub_text: str, test_text: str, gate_output: str) -> str:
    """Assemble the message the reviewer receives, from the plan artifact only.

    A section the plan does not carry is omitted, never printed as an empty heading or a `None`:
    the reviewer must not read a placeholder as data.
    """
    subtask = _subtask(plan, subtask_id)
    parts = [
        "FUNCTION UNDER REVIEW (the plan's description, authoritative)\n"
        f"id: {subtask.get('id', '')}\n"
        f"kind: {subtask.get('kind', '')}\n"
        f"title: {subtask.get('title', '')}\n"
        f"logic description (goal): {subtask.get('goal', '')}"
    ]
    signatures = (subtask.get("contract") or {}).get("new_signatures") or []
    if signatures:
        parts.append("CONTRACT (the signatures this subtask introduces)\n" + "\n".join(f"- {s}" for s in signatures))
    acceptance = subtask.get("acceptance") or []
    if acceptance:
        rows = [f"- in: {pair.get('in', '')} -> out: {pair.get('out', '')}" for pair in acceptance]
        parts.append("ACCEPTANCE PAIRS (derived from the description, not from the tests)\n" + "\n".join(rows))
    assumptions = plan.get("assumptions") or []
    if assumptions:
        parts.append("ASSUMPTIONS\n" + "\n".join(f"- {a}" for a in assumptions))
    parts.append("STUB (the body the executor starts from)\n" + stub_text)
    parts.append("FROZEN TESTS (frozen before any implementation existed)\n" + test_text)
    if gate_output:
        parts.append("EXPECT-FAIL GATE OUTPUT (the tests run on the stub)\n" + gate_output)
    return "\n\n".join(parts) + "\n"


def parse_verdict(text: str) -> dict:
    """Read the answer and normalise the verdict from the findings.

    A blocker or a major finding is a fail regardless of the declared verdict; a fail with only
    minor findings (or none) is a pass, because nothing in it blocks the executor. Anything the
    vocabulary does not name raises ValueError carrying the offending text.
    """
    answer = extract_json(text)
    if not isinstance(answer, dict):
        raise ValueError(f"the answer is not a JSON object: {str(answer)[:200]}")
    absent = [k for k in VERDICT_KEYS if k not in answer]
    if absent:
        raise ValueError(f"the answer is missing {', '.join(absent)}: {str(answer)[:200]}")
    declared = answer.get("verdict")
    if declared not in ("pass", "fail"):
        raise ValueError(f"verdict must be pass or fail, got {declared!r}")
    findings = answer.get("findings")
    if not isinstance(findings, list):
        raise ValueError(f"findings must be a list, got {type(findings).__name__}")
    for finding in findings:
        if not isinstance(finding, dict):
            raise ValueError(f"a finding is not an object: {str(finding)[:200]}")
        missing = [k for k in FINDING_KEYS if k not in finding]
        if missing:
            raise ValueError(f"a finding is missing {', '.join(missing)}: {json.dumps(finding, ensure_ascii=False)}")
        if finding["severity"] not in SEVERITIES:
            raise ValueError(f"severity must be one of {', '.join(SEVERITIES)}, got {finding['severity']!r}")
        if finding["kind"] not in FINDING_KINDS:
            raise ValueError(f"kind must be one of {', '.join(FINDING_KINDS)}, got {finding['kind']!r}")
    blocking = any(f["severity"] in ("blocker", "major") for f in findings)
    return {"verdict": "fail" if blocking else "pass", "findings": findings}


def decide(verdict: dict) -> str:
    """Name who acts next: the executor on a pass, the specifier on a fail, the architect when the
    plan itself is inconsistent."""
    findings = verdict.get("findings") or []
    if verdict.get("verdict") == "fail" and any(f.get("kind") == "plan_inconsistent" for f in findings):
        return "architect"
    return "executor" if verdict.get("verdict") == "pass" else "specifier"


def extract_json(text: str) -> dict:
    """Pull the first complete JSON object out of a model answer (see plan_task.extract_json)."""
    if not text or not text.strip():
        raise ValueError("the model returned an empty answer")
    cleaned = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.MULTILINE).strip()
    start = cleaned.find("{")
    if start < 0:
        raise ValueError(f"no JSON object in the answer: {text[:300]}")
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
    raise ValueError(f"unterminated JSON object in the answer: {text[:300]}")


# ---------------------------------------------------------------- the artefacts
def load_plan(path: Path) -> dict:
    """The plan artifact: `plan_task.py` wraps the plan under a `plan` key; a bare plan also works."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return data


def read_stub_and_tests(worktree: Path, plan: dict, subtask_id: str) -> tuple[str, str]:
    """Read the stub and the test file the plan names for this subtask, by kind, not by path.

    The plan declares each subtask's `kind` (`code` produces the stub, `tests` the frozen test
    file), so a pair is picked from the kinds and never guessed from a directory name.
    """
    subtask = _subtask(plan, subtask_id)
    files = [f for f in subtask.get("files_allowed") or [] if isinstance(f, str) and f]
    if not files:
        raise SystemExit(f"ERROR: subtask {subtask_id!r} has an empty files_allowed")
    plan_part = _plan_part(plan)
    plan_tests = sorted(
        {f for s in plan_part.get("subtasks") or [] for f in s.get("files_allowed") or [] if "/tests/" in f"/{f}"}
    )
    subtask_kinds = {s.get("kind") for s in plan_part.get("subtasks") or []}
    if subtask_kinds and subtask_kinds <= {"code", "tests", "docs"} and len(subtask_kinds) > 1:
        test_rel = next(
            (
                f
                for s in plan_part.get("subtasks") or []
                if s.get("kind") == "tests"
                for f in s.get("files_allowed") or []
            ),
            None,
        )
        stub_rel = next(
            (
                f
                for s in plan_part.get("subtasks") or []
                if s.get("kind") == "code"
                for f in s.get("files_allowed") or []
            ),
            None,
        )
    else:
        test_rel = next((f for f in files if f in plan_tests), None)
        stub_rel = next((f for f in files if f not in plan_tests), None)
    if not test_rel or not stub_rel:
        raise SystemExit(
            f"ERROR: the plan does not name both a stub and a test file for {subtask_id!r}; "
            f"files_allowed: {', '.join(files)}; the plan's test paths: {', '.join(plan_tests)}"
        )
    stub, tests = worktree / stub_rel, worktree / test_rel
    for path in (stub, tests):
        if not path.is_file():
            raise SystemExit(f"ERROR: {path} does not exist in the worktree")
    return stub.read_text(encoding="utf-8"), tests.read_text(encoding="utf-8")


def record_usage(slug: str, subtask_id: str, model: str, usage: dict) -> None:
    """Append the call to the ledger the way every tool does (`python -m rig.usage record`)."""
    prompt_tokens = int(usage.get("prompt_tokens") or 0)
    completion_tokens = int(usage.get("completion_tokens") or 0)
    cached = int((usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0)
    cmd = [
        sys.executable,
        "-m",
        "rig.usage",
        "record",
        "-p",
        slug,
        "-f",
        f"_review:{subtask_id}",
        "-t",
        subtask_id,
        "-r",
        "test_reviewer",
        "-m",
        model,
        "--input",
        str(prompt_tokens),
        "--output",
        str(completion_tokens),
        "--note",
        "test_review.py",
    ]
    if cached:
        cmd += ["--cache-read", str(cached)]
    subprocess.run(cmd, capture_output=True, text=True, cwd=PKG_ROOT)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Review one frozen function's tests against the plan before the executor starts."
    )
    parser.add_argument("--project", required=True, help="project slug")
    parser.add_argument("--plan", required=True, help="plan.json produced by plan_task.py")
    parser.add_argument("--subtask", required=True, help="id of the subtask to review")
    parser.add_argument("--worktree", required=True, help="git worktree holding the frozen artefacts")
    parser.add_argument(
        "--gate-output", help="file with the expect-fail gate output; omitted when the gate did not run"
    )
    parser.add_argument("--out", help="write the raw answer next to the artefacts; nothing is written without it")
    parser.add_argument("--json", action="store_true", help="print the verdict as one JSON object (the default)")
    args = parser.parse_args()

    plan = load_plan(Path(args.plan))
    worktree = Path(args.worktree).expanduser().resolve()
    gate_output = Path(args.gate_output).read_text(encoding="utf-8") if args.gate_output else ""
    stub_text, test_text = read_stub_and_tests(worktree, plan, args.subtask)
    user_message = build_user(plan, args.subtask, stub_text, test_text, gate_output)

    cfg = project_config(args.project)
    raw, usage = call_model(cfg, system_prompt(), user_message)
    if args.out:
        out_path = Path(args.out).expanduser()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(raw, encoding="utf-8")
    record_usage(args.project, args.subtask, cfg["model"], usage or {})

    try:
        parsed = parse_verdict(raw)
    except ValueError as exc:
        print(f"ERROR: unparseable answer: {exc}", file=sys.stderr)
        print(raw, file=sys.stderr)
        return 2
    next_role = decide(parsed)
    result = {
        "verdict": parsed["verdict"],
        "who_next": next_role,
        "findings": parsed["findings"],
        "model": cfg["model"],
        "usage": {
            "prompt_tokens": (usage or {}).get("prompt_tokens"),
            "completion_tokens": (usage or {}).get("completion_tokens"),
        },
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=1))
    else:
        names = {
            "executor": "the executor starts",
            "specifier": "back to the specifier",
            "architect": "to the architect",
        }
        print(f"{parsed['verdict']} — {names[next_role]} ({len(parsed['findings'])} findings)")
    return 0 if parsed["verdict"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
