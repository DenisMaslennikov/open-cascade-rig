#!/usr/bin/env python3
"""review_fix_loop.py — review, hand the findings to the executor, review again, stop at 3 rounds.

Why a loop and not one review: a finding that nobody fixes is a review that did nothing, and a
reviewer that keeps finding the same thing three times is a task that needs a human, not a fourth
attempt. The contract:

  1. `run_role.py --role reviewer` reads the branch read-only and writes report.md.
  2. Findings tagged `blocker`/`major` go to `run_subtask.py --feedback` (the executor fixes them;
     the tests stay frozen).
  3. The branch is reviewed again. No blocking findings → done.
  4. After `--max-rounds` (default 3) rounds with blocking findings → escalate to the owner
     (`notify.py`) and exit 1. Minor findings never trigger a round: work for work's sake is
     explicitly not wanted.

CLI:
  review_fix_loop.py --project occc --plan plan.json --subtask ID --worktree WT
                     [--max-rounds 3] [--role reviewer|reviewer_final] [--model M]
                     [--python PY] [--notify] [--dry-run]

Output: one JSON object on stdout. Exit 0 — reviewed clean (or nothing to fix),
1 — escalated or a step failed.
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

BIN = Path(__file__).resolve().parent
STATE = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local" / "state")) / "cascade"
BLOCKING = {"blocker", "major"}
FINDING = re.compile(r"^\s*[-*]\s*\[(blocker|major|minor)\]\s*(.+)$", re.I)


def run_json(cmd: list[str], cwd: Path, timeout: int) -> tuple[dict, int, str]:
    """Run a pipeline script and parse the trailing JSON object it prints."""
    proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    text = proc.stdout
    start = text.rfind("\n{\n")
    if start < 0 and text.lstrip().startswith("{"):
        start = text.index("{") - 1
    payload: dict = {}
    if start >= 0:
        try:
            payload = json.loads(text[start:])
        except json.JSONDecodeError:
            payload = {}
    return payload, proc.returncode, (text + proc.stderr)[-1500:]


def parse_findings(report: str) -> list[dict]:
    findings = []
    for line in report.splitlines():
        match = FINDING.match(line)
        if match:
            findings.append({"severity": match.group(1).lower(), "text": match.group(2).strip()})
    return findings


def review_brief(project: str, subtask: str, worktree: Path) -> Path:
    """The reviewer's task text: what to read, and what counts as a finding here."""
    out = STATE / project / "requests" / f"{subtask}-review-brief.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        f"# Review subtask {subtask}\n\n"
        f"Worktree: {worktree}\n\n"
        "Read the branch against `origin/main`: `git log --oneline origin/main..HEAD`, "
        "`git diff origin/main...HEAD`, then every touched file in full and the modules that call "
        "it. Run the test suite and the linter; do not modify a single file.\n\n"
        "Report only what is genuinely wrong or will hurt the project later: a wrong result the "
        "tests do not catch, a test that asserts the implementation instead of the requirement, a "
        "broken contract (missing export, changed signature, error class callers cannot catch), "
        "scope creep, or a docstring that promises what the code does not do. Tag each finding "
        "`[blocker]`, `[major]` or `[minor]` under `## Findings`.\n\n"
        "Be economical, in this exact order: (1) one `git diff origin/main...HEAD`, (2) at most four "
        "file reads of what the diff touches, (3) write the report. Do not run the test suite — the "
        "pipeline already proved it green, and every extra tool call re-sends the whole conversation "
        "to the model, which is what makes a review expensive. If you run out of turns, the run is "
        "a failure even if you understood everything.\n\n"
        "If the code is fine, write `## Findings` with the single line `- none` — an empty review is "
        "a valid review, and an invented finding is worse than none. The `## Что проверить человеку` "
        "section is optional: omit it when there is nothing a human must decide.\n",
        encoding="utf-8",
    )
    return out


def notify(text: str) -> None:
    subprocess.run([sys.executable, str(BIN / "notify.py"), "--text", text], capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser(description="Review, fix, re-review; escalate after N rounds.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--subtask", required=True)
    ap.add_argument("--worktree", required=True)
    ap.add_argument("--max-rounds", type=int, default=3)
    ap.add_argument(
        "--engine",
        default="pr",
        choices=("pr", "agent"),
        help="pr = one pr-agent call over the plain diff (cheap, the right default for "
        "a subtask); agent = read-only OpenHands reviewer that reads the "
        "repository (expensive per turn: the whole conversation is resent)",
    )
    ap.add_argument("--role", default="reviewer", choices=("reviewer", "reviewer_final"))
    ap.add_argument("--model", help="override the reviewer model")
    ap.add_argument(
        "--budget",
        type=float,
        default=None,
        help="per-round agent budget in USD; a review that reads a repository costs "
        "more than the 2.0 default of run_role and dies mid-read",
    )
    ap.add_argument(
        "--iterations",
        type=int,
        default=12,
        help="agent iteration cap per round. The cost of a review is dominated by "
        "re-sending the conversation each turn: 30+ iterations on one diff means "
        "the reviewer is browsing, not reviewing",
    )
    ap.add_argument(
        "--fix-model",
        help="model for the rework pass (the executor gate stays the "
        "same); the owner wants a cheap flash model for fixes",
    )
    ap.add_argument("--python", help="interpreter that can import the backend dependencies")
    ap.add_argument("--notify", action="store_true", help="send the escalation to Telegram")
    ap.add_argument("--dry-run", action="store_true", help="review once, fix nothing")
    args = ap.parse_args()

    worktree = Path(args.worktree).expanduser().resolve()
    brief = review_brief(args.project, args.subtask, worktree)
    rounds: list[dict] = []
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")

    for round_number in range(1, args.max_rounds + 1):
        if args.engine == "pr":
            # One call over the plain diff. The reviewer reads the diff, not the whole repository:
            # an agent that re-reads the repo spends dollars on re-sending the conversation.
            diff_file = STATE / args.project / "reviews" / f"{args.subtask}-round{round_number}.diff"
            diff_file.parent.mkdir(parents=True, exist_ok=True)
            diff_text = subprocess.run(
                ["git", "diff", "origin/main...HEAD"], cwd=worktree, capture_output=True, text=True
            ).stdout
            diff_file.write_text(diff_text, encoding="utf-8")
            review_cmd = [
                sys.executable,
                str(BIN / "pr_review.py"),
                "--project",
                args.project,
                "--mode",
                "diff",
                "--diff",
                str(diff_file),
                "--what",
                f"subtask {args.subtask} of the OCCC cascade",
            ]
            if args.role == "reviewer_final":
                review_cmd.append("--final")
            if args.model:
                review_cmd += ["--model", args.model]
            review, code, tail = run_json(review_cmd, worktree, timeout=3600)
            report_path = review.get("review")
        else:
            review_cmd = [
                sys.executable,
                str(BIN / "run_role.py"),
                "--project",
                args.project,
                "--role",
                args.role,
                "--request",
                f"@{brief}",
                "--worktree",
                str(worktree),
            ]
            if args.model:
                review_cmd += ["--model", args.model]
            review_cmd += ["--iterations", str(args.iterations)]
            if args.budget is not None:  # no cap unless the operator asked for one
                review_cmd += ["--budget", str(args.budget)]
            review, code, tail = run_json(review_cmd, worktree, timeout=3600)
            report_path = review.get("report")
        report = Path(report_path).read_text(encoding="utf-8") if report_path and Path(report_path).exists() else ""
        findings = parse_findings(report)
        blocking = [f for f in findings if f["severity"] in BLOCKING]
        if args.engine == "pr" and not findings and review.get("verdict") == "block":
            # pr-agent does not tag severities consistently; its own verdict is the signal.
            blocking = [{"severity": "blocker", "text": "pr-agent verdict: block — see the report"}]
            findings = blocking
        record = {
            "round": round_number,
            "review_ok": bool(review.get("ok")),
            "model": review.get("model"),
            "report": report_path,
            "findings": findings,
            "blocking": len(blocking),
            "usage": review.get("usage"),
        }
        rounds.append(record)
        # An unparsed answer is a failed review, not a clean one: silence must never read as
        # "nothing to fix" (that is how a wrong diff gets merged with a green light).
        if not review.get("ok") or not report_path or not Path(report_path).exists():
            record["error"] = f"the review produced no usable answer (exit {code}); stdout tail: {tail[-400:]}"
            print(
                json.dumps(
                    {
                        "project": args.project,
                        "subtask": args.subtask,
                        "rounds": rounds,
                        "status": "failed",
                        "stamp": stamp,
                    },
                    ensure_ascii=False,
                    indent=1,
                )
            )
            return 1
        if code != 0 and not findings:
            record["error"] = f"the review run failed (exit {code}) without findings: {tail[-500:]}"
            print(
                json.dumps(
                    {
                        "project": args.project,
                        "subtask": args.subtask,
                        "rounds": rounds,
                        "status": "failed",
                        "stamp": stamp,
                    },
                    ensure_ascii=False,
                    indent=1,
                )
            )
            return 1
        if not blocking or args.dry_run:
            status = "clean" if not blocking else "reviewed (dry run)"
            print(
                json.dumps(
                    {
                        "project": args.project,
                        "subtask": args.subtask,
                        "rounds": rounds,
                        "status": status,
                        "stamp": stamp,
                    },
                    ensure_ascii=False,
                    indent=1,
                )
            )
            return 0

        feedback = "\n".join(f"- [{f['severity']}] {f['text']}" for f in blocking)
        fix_cmd = [
            sys.executable,
            str(BIN / "run_subtask.py"),
            "--project",
            args.project,
            "--plan",
            args.plan,
            "--subtask",
            args.subtask,
            "--worktree",
            str(worktree),
            "--feedback",
            feedback,
        ]
        if args.python:
            fix_cmd += ["--python", args.python]
        if args.fix_model:
            fix_cmd += ["--model", args.fix_model]
        fix, fix_code, fix_tail = run_json(fix_cmd, worktree, timeout=3600)
        record["fix"] = {
            "exit": fix_code,
            "ok": bool(fix.get("ok")),
            "commit": fix.get("commit"),
            "error": fix.get("error"),
            "tail": None if fix_code == 0 else fix_tail[-500:],
        }
        if fix_code != 0:
            rounds[-1]["fix_failed"] = True
            break

    # The escalation is read by the owner in Telegram, in Russian: what was reviewed, what was
    # already fixed, and what still stands in the way. English artefacts stay in the state dir.
    done = []
    for item in rounds:
        fixes = item.get("fix") or {}
        applied = (
            ("правка применена, коммит " + str(fixes.get("commit"))[:7])
            if fixes.get("ok")
            else ("правка не прошла: " + str(fixes.get("error") or "см. лог"))
            if fixes
            else "правок не было"
        )
        done.append(
            f"  • итерация {item['round']}: ревьюер {item.get('model') or '—'}, "
            f"находок {len(item.get('findings') or [])}, блокирующих {item['blocking']} — {applied}"
        )
    last = rounds[-1] if rounds else {}
    still = [f["text"] for f in (last.get("findings") or []) if f["severity"] in BLOCKING]
    message = (
        f"OCCC · {args.subtask}: ревью не пройдено за {len(rounds)} итерации — нужно решение человека.\n"
        + "\n".join(done)
        + ("\nЧто мешает пройти: " + still[0][:300] if still else "\nЧто мешает пройти: ревьюер не выдал отчёт.")
        + f"\nОтчёт: {last.get('report') or '—'}"
    )
    if args.notify:
        notify(message)
    print(
        json.dumps(
            {
                "project": args.project,
                "subtask": args.subtask,
                "rounds": rounds,
                "status": "escalated",
                "message": message,
                "stamp": stamp,
            },
            ensure_ascii=False,
            indent=1,
        )
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
