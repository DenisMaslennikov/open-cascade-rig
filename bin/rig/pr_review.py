#!/usr/bin/env python3
"""pr_review.py — run the pr-agent reviewer over a prepared diff and keep the result.

The reviewer role is pr-agent (D7). It runs in plain-diff mode: the orchestrator decides what the
reviewer sees, pr-agent produces the review, nothing is posted to GitHub behind the owner's back.

Modes:
  schema — the contract rendered as a readable document (docs/schema/schema.md), one synthetic file
  docs   — the PR's prose files only (README, rules, AGENTS), generated data excluded

CLI:
  pr_review.py --project <slug> --mode schema|docs [--pr N] [--model MODEL] [--repo PATH]

Output (stdout, one JSON object): {"ok": true, "review": path, "usage": {...}, "verdict": hint}
Exit codes: 0 — review produced, 1 — failure.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from .plan_task import CONFIG, STATE, call_model_stream, extract_json, load_keys, parse_simple_yaml
from .rig_conf import gateway, project_config

BIN = Path(__file__).resolve().parent
PKG_ROOT = BIN.parent

REVIEW_BINARY_ENV = "CASCADE_REVIEW_BINARY"
SETUP_TOOL = "setup_pr_agent.py"
DATA_FILES = ("docs/schema/schema.json", "docs/schema/schema.dbml")
HUMAN_SECTION_HINT = (
    "After the findings, add a section titled exactly «Что проверить человеку» with 3 to 5 "
    "bullets. Each bullet names the place (path, table or card) and the question the human has to "
    "answer himself, because a machine cannot: a product decision, a licence or security judgement, "
    "a data-loss risk that only the owner can accept, or a claim that needs a fact from outside the "
    "repository. No generic advice like «проверьте тесты» — only what a machine could not settle."
)


def key_of(slug: str) -> str:
    project = parse_simple_yaml(CONFIG / "projects" / f"{slug}.yaml")
    ref = project.get("_", {}).get("key_ref") or "LITELLM_KEY"
    value = load_keys().get(ref, "")
    if not value:
        raise SystemExit(f"ERROR: {ref} is missing from {CONFIG / 'keys.env'}")
    return value


def pr_agent_binary(slug: str) -> Path:
    """The reviewer binary for `slug`: project config `reviewer_binary`, then $CASCADE_REVIEW_BINARY.

    The first source that names a path owns the answer: a configured path that does not exist is an
    error naming that path, never a silent fall-through to the next source. With nothing configured
    the error tells the operator which key, which project and which tool fix it.
    """
    configured = project_config(slug).get("_", {}).get("reviewer_binary")
    if configured:
        return _existing_binary(Path(configured).expanduser(), f"reviewer_binary for {slug}")
    from_env = os.environ.get(REVIEW_BINARY_ENV)
    if from_env:
        return _existing_binary(Path(from_env).expanduser(), REVIEW_BINARY_ENV)
    raise SystemExit(
        f"ERROR: no reviewer binary for {slug}. Set `reviewer_binary:` in {CONFIG}/projects/{slug}.yaml "
        f"(or export {REVIEW_BINARY_ENV}); bin/{SETUP_TOOL} creates one."
    )


def _existing_binary(path: Path, source: str) -> Path:
    if not path.exists():
        raise SystemExit(f"ERROR: {source} points at {path}, which does not exist")
    return path


def reviewer_model(slug: str, override: str | None, final: bool = False) -> str:
    """Model for a review run: `reviewer` for a small diff, `reviewer_final` for the PR.

    Two reviewer models is the owner's rule: a fast vendor checks every subtask diff, a stronger
    (and slower) vendor reads the finished PR, so the blind spots of the executor are covered at
    both levels and the expensive model is not spent on trivial diffs.
    """
    if override:
        return override
    project = parse_simple_yaml(CONFIG / "projects" / f"{slug}.yaml")
    models = project.get("models") or {}
    role = "reviewer_final" if final else "reviewer"
    model = models.get(role) or (models.get("reviewer", "") if final else "")
    if not model:
        raise SystemExit(f"ERROR: {role} model is not set for {slug}")
    return model


def gh(*args: str, cwd: Path) -> str:
    out = subprocess.run(["gh", *args], capture_output=True, text=True, cwd=cwd)
    if out.returncode != 0:
        raise SystemExit(f"ERROR: gh {' '.join(args)}: {out.stderr.strip()[:300]}")
    return out.stdout


def added_file_diff(path: str, content: str) -> str:
    lines = content.splitlines()
    body = "\n".join("+" + line for line in lines)
    return (
        f"diff --git a/{path} b/{path}\nnew file mode 100644\n--- /dev/null\n"
        f"+++ b/{path}\n@@ -0,0 +1,{len(lines)} @@\n{body}\n"
    )


def build_diff(
    mode: str, repo: Path, slug: str, pr: int | None, diff_file: str | None = None, what: str | None = None
) -> tuple[str, str]:
    if mode == "diff":
        if not diff_file:
            raise SystemExit("ERROR: --diff is required for mode diff")
        path = Path(diff_file)
        if not path.is_absolute():
            path = repo / path
        if not path.exists():
            raise SystemExit(f"ERROR: no diff file at {path}")
        return path.read_text(encoding="utf-8"), what or "the changes of this branch"
    if mode == "schema":
        rendered = STATE / slug / "schema" / "schema.md"
        if not rendered.exists():
            raise SystemExit(f"ERROR: no rendered schema at {rendered} (run schema_design.py)")
        return added_file_diff(
            "docs/schema/SCHEMA.md", rendered.read_text(encoding="utf-8")
        ), "the database schema contract rendered as a document"
    if mode == "docs":
        if not pr:
            raise SystemExit("ERROR: --pr is required for mode docs")
        diff = gh("pr", "diff", str(pr), cwd=repo)
        chunks = re.split(r"(?m)^diff --git ", diff)
        kept = []
        for chunk in chunks:
            if not chunk.strip():
                continue
            header = "diff --git " + chunk
            target = chunk.split(" b/", 1)[-1].split("\n", 1)[0].strip()
            if target in DATA_FILES:
                continue
            kept.append(header)
        return "".join(kept), "the prose part of the pull request"
    raise SystemExit(f"ERROR: unknown mode {mode!r}")


def runs_dir(slug: str) -> Path:
    path = STATE / slug / "reviews" / "runs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def status(slug: str) -> int:
    """Show every review run: alive or not, how old, when its log last moved."""
    now = datetime.now(UTC)
    rows = []
    for record in sorted(runs_dir(slug).glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:10]:
        data = json.loads(record.read_text(encoding="utf-8"))
        pid = data.get("pid")
        alive = False
        if pid:
            try:
                os.kill(pid, 0)
                alive = True
            except OSError:
                alive = False
        log = Path(data.get("log", ""))
        log_age = (now - datetime.fromtimestamp(log.stat().st_mtime, UTC)).seconds if log.exists() else None
        started = datetime.fromisoformat(data["started"])
        elapsed = int((now - started).total_seconds())
        if data.get("finished"):
            state = "done"
        elif alive and log_age is not None and log_age > 300:
            state = "waiting_on_model"
        elif alive:
            state = "running"
        else:
            state = "dead"
        rows.append(
            {
                "run": record.stem,
                "model": data.get("model"),
                "mode": data.get("mode"),
                "state": state,
                "elapsed_s": elapsed,
                "log_age_s": log_age,
                "pid": pid,
                "log": data.get("log"),
            }
        )
    print(json.dumps({"project": slug, "runs": rows}, ensure_ascii=False, indent=1))
    return 0


def ask_human_checks(cfg: dict, review_markdown: str) -> tuple[list[str], dict]:
    """Second, short call: what must the human decide himself, given this review.

    Answered in Russian: this list is read by the owner, not by another model, so it is a
    human-facing summary, not a pipeline artefact.
    """

    prompt = (
        "Below is a code review produced by a reviewer. List 3 to 5 points that a human must check "
        "himself because a machine cannot settle them: a product decision, a licence or security "
        "judgement, a data-loss risk only the owner can accept, or a claim that needs a fact from "
        "outside the repository. Each point names the place and states the question. "
        "Write the points in Russian, one sentence each, no generic advice like «проверьте тесты». "
        "If nothing needs a human decision — the review is clean or everything is machine-checkable "
        "— return an empty list. Never invent a point to look thorough. "
        'Answer as JSON: {"checks": []} and nothing else.\n\nREVIEW\n' + review_markdown[:6000]
    )
    content, usage = call_model_stream(
        cfg, "You extract open questions for a human reviewer.", prompt, idle_timeout=180
    )
    try:
        answer = extract_json(content)
    except SystemExit:
        return [], usage
    checks = [str(item).strip() for item in answer.get("checks") or [] if str(item).strip()]
    return checks[:5], usage


def extract_human_checks(markdown: str) -> list[str]:
    """Pull the reviewer's list of things only a human can decide out of the review."""
    lines = markdown.splitlines()
    start = None
    for index, line in enumerate(lines):
        stripped = line.strip().strip("#*").strip().lower()
        if stripped.startswith(("что проверить человеку", "what the human must check", "what to check")):
            start = index + 1
            break
    if start is None:
        return []
    checks: list[str] = []
    for line in lines[start:]:
        stripped = line.strip().strip("*_ ")
        if not stripped:
            if checks:
                break
            continue
        if stripped.startswith("#"):
            break
        if stripped[:2] in ("- ", "* ") or stripped[:3].rstrip(".").isdigit():
            item = stripped.lstrip("-*0123456789. ").strip()
            if item:
                checks.append(item)
        elif checks:
            break
    return checks


def main() -> int:
    parser = argparse.ArgumentParser(description="Review a prepared diff with pr-agent.")
    parser.add_argument("--project", required=True)
    parser.add_argument("--status", action="store_true", help="list review runs and their health")
    parser.add_argument(
        "--max-output", type=int, default=2500, help="cap on completion tokens per call (config.max_output_tokens)"
    )
    parser.add_argument(
        "--ai-timeout", type=int, default=600, help="per-request model timeout inside pr-agent (config.ai_timeout)"
    )
    parser.add_argument("--mode", choices=("schema", "docs", "diff"))
    parser.add_argument("--pr", type=int)
    parser.add_argument("--diff", help="unified diff file for --mode diff")
    parser.add_argument("--what", help="one-line description of the changes under review")
    parser.add_argument("--model", help="override the reviewer model (debug on a cheap one)")
    parser.add_argument(
        "--final", action="store_true", help="final PR review: use models.reviewer_final and a longer wait"
    )
    parser.add_argument("--timeout", type=int, help="seconds to wait for pr-agent (default 1900, --final 3600)")
    parser.add_argument(
        "--notify", action="store_true", help="send the Russian «на что обратить внимание» list to Telegram"
    )
    parser.add_argument("--repo", help="repository path; defaults to ~/projects/<slug>")
    parser.add_argument("--extra", default="", help="extra instruction for the reviewer")
    args = parser.parse_args()

    if args.status:
        return status(args.project)
    if not args.mode:
        parser.error("--mode is required unless --status is given")

    wait_seconds = args.timeout or (3600 if args.final else 1900)
    if args.final and args.ai_timeout == 600:
        args.ai_timeout = 1800

    repo = Path(os.path.expanduser(args.repo or f"~/projects/{args.project}"))
    model = reviewer_model(args.project, args.model, final=args.final)
    out_dir = STATE / args.project / "reviews"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    tag = f"{args.mode}-{stamp}"
    diff_path = out_dir / f"{tag}.diff"
    review_md = out_dir / f"{tag}.md"
    review_json = out_dir / f"{tag}.json"
    log_path = out_dir / f"{tag}.log"

    diff, what = build_diff(args.mode, repo, args.project, args.pr, args.diff, args.what)
    diff_path.write_text(diff, encoding="utf-8")

    token = subprocess.run(["gh", "auth", "token"], capture_output=True, text=True).stdout.strip()
    env = dict(os.environ)
    env.update(
        {
            "CONFIG__MODEL": f"openai/{model}",
            # pr-agent refuses any model it does not know unless this is set, so it is not an output cap
            # for our own calls — it is the reviewer's context budget. Override per run when needed.
            "CONFIG__CUSTOM_MODEL_MAX_TOKENS": os.environ.get("CASCADE_REVIEW_MODEL_MAX_TOKENS", "32000"),
            "CONFIG__RESPONSE_LANGUAGE": "ru-RU",
            "CONFIG__AI_TIMEOUT": str(args.ai_timeout),
            "CONFIG__MAX_OUTPUT_TOKENS": str(args.max_output),
            "CONFIG__PUBLISH_OUTPUT": "false",
            "CONFIG__PERSISTENT_COMMENT": "false",
            "GITHUB__USER_TOKEN": token,
            "GITHUB__DEPLOYMENT_TYPE": "user",
            "OPENAI__KEY": key_of(args.project),
            "OPENAI__API_BASE": gateway(project_config(args.project), script="pr_review"),
        }
    )
    record_path = runs_dir(args.project) / f"{tag}.json"
    record_path.write_text(
        json.dumps(
            {
                "mode": args.mode,
                "model": model,
                "pid": None,
                "started": datetime.now(UTC).isoformat(timespec="seconds"),
                "log": str(log_path),
                "diff": str(diff_path),
                "finished": False,
            },
            ensure_ascii=False,
            indent=1,
        )
        + "\n",
        encoding="utf-8",
    )

    def close_record(patch: dict) -> None:
        current = json.loads(record_path.read_text(encoding="utf-8"))
        current.update(patch)
        record_path.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")

    # The binary comes from configuration and may be gone (a pruned cache); that is an operator
    # error, not a traceback: the run record keeps the reason and the failure is an exit code.
    try:
        binary = pr_agent_binary(args.project)
    except SystemExit as exc:
        close_record({"finished": True, "exit": 1, "error": str(exc)})
        print(str(exc), file=sys.stderr)
        return 1
    cmd = [str(binary), f"--diff-file={diff_path}", f"--output={review_md}", f"--json-output={review_json}", "review"]
    # The reviewer's answer is validated against pr-agent's review schema; a free-form instruction
    # with a colon is parsed by dynaconf as a nested setting and the answer stops validating.
    # The human-facing section is asked for afterwards, from the finished review text.
    extra = (args.extra or "").strip().replace(": ", " — ")
    if extra:
        cmd.append(f"--pr_reviewer.extra_instructions={extra}")
    # stream the log so the run is observable while the model is thinking
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, stdout=log, stderr=subprocess.STDOUT, text=True, env=env, cwd=repo)
        close_record({"pid": proc.pid})
        try:
            proc.wait(timeout=wait_seconds)
        except subprocess.TimeoutExpired:
            proc.kill()
    current = json.loads(record_path.read_text(encoding="utf-8"))
    current.update({"finished": True, "exit": proc.returncode})
    record_path.write_text(json.dumps(current, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if proc.returncode != 0 or not review_md.exists():
        print(f"ERROR: pr-agent failed (exit {proc.returncode}); log {log_path}", file=sys.stderr)
        print(log_path.read_text(encoding="utf-8")[-800:], file=sys.stderr)
        return 1

    usage = {}
    if review_json.exists():
        try:
            parsed = json.loads(review_json.read_text(encoding="utf-8"))
            usage = parsed.get("usage") or {}
        except json.JSONDecodeError:
            pass
    text = review_md.read_text(encoding="utf-8")
    human_checks = extract_human_checks(text)
    if not human_checks:
        llm = {
            "model": model,
            "key": key_of(args.project),
            "base_url": gateway(project_config(args.project), script="pr_review"),
            "temperature": 0.0,
            "max_tokens": 900,
        }
        try:
            human_checks, extra_usage = ask_human_checks(llm, text)
        except SystemExit as exc:  # the section is optional; losing the whole review over it is not
            print(f"note: no human-checks section ({exc}); the review itself stands", file=sys.stderr)
            human_checks, extra_usage = [], {}
        for key_name, value in extra_usage.items():
            if isinstance(value, int):
                usage[key_name] = int(usage.get(key_name) or 0) + value
    # A reviewer that could not review is not an approving reviewer. pr-agent writes a short
    # "Failed to review PR" and exits 0, which used to read as `approve` on a tiny answer.
    if re.search(r"failed to review|error reviewing", text, re.I) or "\n" not in text.strip():
        print(f"ERROR: the reviewer produced no review (see {review_md}): {text.strip()[:300]}", file=sys.stderr)
        return 1
    verdict = (
        "block" if re.search(r"🔴|blocker|критич", text, re.I) else "comment" if len(text.strip()) > 200 else "approve"
    )
    summary = {
        "ok": True,
        "mode": args.mode,
        "target": what,
        "model": model,
        "review": str(review_md),
        "diff": str(diff_path),
        "log": str(log_path),
        "verdict": verdict,
        "human_checks": human_checks,
        "usage": usage,
    }
    if usage.get("total_tokens"):
        subprocess.run(
            [
                sys.executable,
                "-m",
                "rig.usage",
                "record",
                "-p",
                args.project,
                "-f",
                f"_review:{args.mode}",
                "-t",
                f"{args.mode}",
                "-r",
                "reviewer_final" if args.final else "reviewer",
                "-m",
                model,
                "--input",
                str(usage.get("prompt_tokens") or usage.get("input_tokens") or 0),
                "--output",
                str(usage.get("completion_tokens") or usage.get("output_tokens") or 0),
                "--note",
                "pr_review.py",
            ],
            capture_output=True,
            text=True,
            cwd=PKG_ROOT,
        )
    # The console block is what the owner reads — often on a phone, where a file path is useless.
    # So the message carries the review text itself (findings, not links), and the paths come last.
    scope = "финальное ревью PR" if args.final else "ревью подзадачи"
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"&nbsp;?", " ", plain)
    plain = re.sub(r"\n{2,}", "\n", re.sub(r"[ \t]{2,}", " ", plain)).strip()
    lines = [f"Ревью ({scope}) — {model}", f"вердикт: {verdict}", "", plain[:1800], ""]
    if human_checks:
        lines.append("На что обратить внимание (по мнению ревьюера):")
        lines.extend(f"{index}. {item}" for index, item in enumerate(human_checks, 1))
    # No section is a normal outcome, not a gap to fill: the owner asked for no invented points.
    lines.append(f"файлы: diff {diff_path.name}, отчёт {review_md.name}")
    report = "\n".join(lines)
    print(report)
    if args.notify:
        subprocess.run(
            [sys.executable, "-m", "rig.notify", "--text", report], capture_output=True, text=True, cwd=PKG_ROOT
        )
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
