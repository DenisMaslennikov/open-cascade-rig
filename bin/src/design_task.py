#!/usr/bin/env python3
"""design_task.py — run the designer role on one interface task and keep hand-rolled HTML.

The designer does not write application code. Its deliverable is a set of self-contained HTML
screens (inline CSS, real token values) plus a handoff note that names every component, its
states and the tokens it uses, so that the executor can rebuild the screen inside the React
application without making visual decisions of its own.

CLI:
  design_task.py --project <slug> --request "text|@file" --worktree PATH
                 [--out docs/design/<slug>] [--model MODEL] [--screenshots] [--dry-run]
  design_task.py --project <slug> --status

Output (stdout, one JSON object):
  {"ok": bool, "dir": path, "files": [...], "screens": [...], "shots": [...],
   "commit": sha|null, "usage": {...}}

State:
  ~/.local/state/cascade/<slug>/designs/<ts>/{record.json,raw.txt,prompt.md}

Exit codes: 0 — artefacts written and committed; 1 — failure.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from .rig_conf import gateway

BIN = Path(__file__).resolve().parent
from .plan_task import (  # noqa: E402
    CONFIG,
    STATE,
    call_model_stream,
    extract_json,
    load_keys,
    parse_simple_yaml,
    repo_map,
    token_limit,
)

SYSTEM_PROMPT = """You are the designer of an automated development pipeline.

Your job: turn ONE interface task into (a) hand-written, self-contained HTML screens that can be
opened in a browser without a build step and (b) a handoff note precise enough for a coding agent
to rebuild the same screen inside the existing React application.

Answer with the files themselves, in exactly this layout and nothing before or after it:

<<<FILE docs/design/<slug>/skill-plan.html>>>
<!DOCTYPE html>
... the whole file, as plain text, no escaping ...
<<<END>>>
<<<FILE docs/design/<slug>/README.md>>>
... the whole file ...
<<<END>>>
<<<SUMMARY>>>
Three to five sentences: what you decided, which tokens you added, what the coding agent must
reproduce first.

Hard rules:
- HTML is standalone: one file, inline <style>, no external fonts, no CDN, no JS frameworks.
  Inline SVG or CSS shapes only; icons are simple SVG glyphs.
- Use the design tokens of the project (names and values from the tokens file you are given).
  Never invent a colour that is not a token; when a new semantic token is needed, name it and say
  so in the handoff.
- Dark theme is the default, as the theme file says.
- Every component in the markup carries `data-component="<Name>"`, every interactive element is a
  real <button>/<a>/<input> and is keyboard reachable with a visible focus ring.
- Show every state the task asks for, on purpose: loading, empty, error, no-access, stale.
  A state is a real rendered block, not a sentence in a comment.
- Numbers, ISK and durations are formatted like the product does it (monospace digits, thin
  separators); Russian and English labels both appear where the screen has text, and the note says
  which catalogue keys are needed.
- A README.md in the same directory: screen inventory, component inventory, tokens used and tokens
  that must be added, states per component, and the accessibility checklist you satisfied.
- English for code, comments and the README; Russian only inside the interface strings, as the
  product ships both locales.

You are not writing application code, tests or migrations. HTML, CSS and the note only."""


FILE_BLOCK = re.compile(r"<<<FILE\s+(\S+?)\s*>>>\r?\n(.*?)\r?\n<<<END>>>", re.DOTALL)
SUMMARY_BLOCK = re.compile(r"<<<SUMMARY>>>\s*(.*)$", re.DOTALL)


def parse_blocks(text: str) -> tuple[list[dict], str]:
    """Read the `<FILE …>` block format; it carries HTML without JSON escaping and never breaks on quotes."""
    files = []
    for match in FILE_BLOCK.finditer(text):
        path = match.group(1).strip()
        suffix = Path(path).suffix.lower().lstrip(".")
        kind = {"html": "html", "css": "css", "md": "md", "svg": "svg"}.get(suffix, "html")
        files.append({"path": path, "content": match.group(2), "kind": kind})
    summary_match = SUMMARY_BLOCK.search(text)
    return files, (summary_match.group(1).strip() if summary_match else "")


def is_complete(text: str) -> bool:
    """True when every `<<<FILE …>>>` opened in the text has a closing `<<<END>>>` after it.

    Counting markers is wrong: a model is free to mention `<<<END>>>` in its own prose, and a
    finished answer may carry one more marker than files. What matters is the order.
    """
    starts = [m.start() for m in re.finditer(r"<<<FILE\s", text)]
    if not starts:
        return False
    ends = [m.start() for m in re.finditer(r"<<<END>>>", text)]
    last_end = ends[-1] if ends else -1
    return last_end > starts[-1]


def project_config(slug: str, role: str = "designer") -> dict:
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
        "base_url": gateway(project, script="design_task.py"),
        "temperature": float(params.get("temperature", 0.3)),
        "max_tokens": token_limit(params.get("max_tokens")),
    }


def design_context(worktree: Path) -> str:
    """What the designer must see: the tokens, the theme wiring, the interface rules."""
    parts = []
    for rel in (
        "frontend/src/theme/tokens.ts",
        "frontend/src/theme/antd.ts",
        ".agents/rules/ui.md",
        "frontend/package.json",
    ):
        path = worktree / rel
        if path.exists():
            text = path.read_text(encoding="utf-8", errors="replace")
            parts.append(f"--- {rel} ---\n{text[:12000]}")
    return "\n\n".join(parts)


def record_usage(slug: str, feature: str, model: str, usage: dict) -> None:
    cmd = [
        sys.executable,
        str(BIN / "usage.py"),
        "record",
        "-p",
        slug,
        "-f",
        feature,
        "-t",
        "_design",
        "-r",
        "designer",
        "-m",
        model,
        "--input",
        str(usage.get("prompt_tokens") or 0),
        "--output",
        str(usage.get("completion_tokens") or 0),
        "--note",
        "design_task.py",
    ]
    if (usage.get("prompt_tokens_details") or {}).get("cached_tokens"):
        cmd += ["--cache-read", str(usage["prompt_tokens_details"]["cached_tokens"])]
    subprocess.run(cmd, capture_output=True, text=True)


def never_copy(worktree: Path) -> list[str]:
    """The rules and ADRs must be readable by the designer but never rewritten by it."""
    return []


def write_design(worktree: Path, out_dir: str, files: list[dict]) -> tuple[bool, list[str]]:
    errors: list[str] = []
    allowed_suffix = {".html", ".css", ".md", ".svg"}
    written: list[str] = []
    for item in files:
        path = (item.get("path") or "").strip()
        content = item.get("content")
        if not path.startswith(out_dir.rstrip("/") + "/"):
            errors.append(f"{path}: outside the design directory {out_dir}")
            continue
        if Path(path).suffix.lower() not in allowed_suffix:
            errors.append(f"{path}: only html/css/md/svg artefacts are allowed")
            continue
        if not isinstance(content, str) or not content.strip():
            errors.append(f"{path}: empty content")
            continue
        # some gateways escape newlines twice; a literal \n carries no meaning in html/md
        if "\n" not in content and "\\n" in content:
            content = content.replace("\\n", "\n").replace('\\"', '"')
        target = worktree / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content if content.endswith("\n") else content + "\n", encoding="utf-8")
        written.append(path)
    if not any(p.endswith(".html") for p in written):
        errors.append("no HTML screen produced")
    return not errors, errors


REQUIRED_STATES = ("loaded", "loading", "empty", "error", "no-access", "stale")


def marker_gaps(request: str, html: str) -> list[str]:
    """What the design artefact promised in the brief and did not deliver as markup."""
    gaps = []
    if "data-component" in request and html.count("data-component") < 3:
        gaps.append("the markup carries no data-component markers")
    if "data-status" in request and html.count("data-status") < 2:
        gaps.append("the plan rows carry no data-status markers")
    if "data-nav" in request:
        for variant in ("expanded", "collapsed"):
            if f'data-nav="{variant}"' not in html:
                gaps.append(f'the navigation variant "{variant}" is not rendered')
    if "LIGHT THEME ONLY" in request and 'data-theme="dark"' in html:
        gaps.append("the page still carries a dark theme attribute")
    for state in REQUIRED_STATES:
        if state in request and f'data-state="{state}"' not in html:
            gaps.append(f'the state "{state}" is not rendered as data-state="{state}"')
    return gaps


def render(worktree: Path, html_files: list[str]) -> list[str]:
    """Best-effort PNG of each screen, so a human (and a vision model) can look without a browser."""
    browser = next(
        (
            p
            for p in (
                shutil.which("chromium"),
                shutil.which("chromium-browser"),
                shutil.which("google-chrome"),
                shutil.which("google-chrome-stable"),
            )
            if p
        ),
        None,
    )
    if not browser:
        return []
    shots: list[str] = []
    for rel in html_files:
        shot = worktree / rel
        png = shot.with_suffix(".png")
        proc = subprocess.run(
            [
                browser,
                "--headless=new",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--window-size=1600,1000",
                f"--screenshot={png}",
                shot.as_uri(),
            ],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if proc.returncode == 0 and png.exists():
            shots.append(str(png))
    return shots


def status(slug: str) -> int:
    root = STATE / slug / "designs"
    rows = []
    now = time.time()
    for record in sorted(root.glob("*/record.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:5]:
        data = json.loads(record.read_text(encoding="utf-8"))
        rows.append(
            {
                "started": data.get("started"),
                "state": data.get("state"),
                "model": data.get("model"),
                "age_seconds": int(now - record.stat().st_mtime),
                "dir": str(record.parent),
            }
        )
    print(json.dumps({"runs": rows}, ensure_ascii=False, indent=1))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Run the designer role on one interface task.")
    ap.add_argument("--project", required=True)
    ap.add_argument("--request")
    ap.add_argument("--worktree")
    ap.add_argument("--out", help="design directory, repository-relative")
    ap.add_argument("--model")
    ap.add_argument("--screenshots", action="store_true", help="render PNG next to every screen")
    ap.add_argument("--attempts", type=int, default=2, help="how many times to re-ask when the artefact misses markers")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-raw", help="write the artefacts from a saved raw answer, no model call")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    if args.status:
        return status(args.project)
    if not args.request or not args.worktree:
        raise SystemExit("ERROR: --request and --worktree are required")

    request = args.request
    if request.startswith("@"):
        request = Path(request[1:]).read_text(encoding="utf-8")
    worktree = Path(args.worktree).expanduser().resolve()
    cfg = project_config(args.project)
    if args.model:
        cfg["model"] = args.model

    slug = args.project
    out_dir = args.out or f"docs/design/{slug}"
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    run_dir = STATE / slug / "designs" / stamp
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "record.json").write_text(
        json.dumps(
            {"started": stamp, "state": "waiting for the model", "model": cfg["model"]}, ensure_ascii=False, indent=1
        ),
        encoding="utf-8",
    )

    if args.from_raw:
        raw = Path(args.from_raw).expanduser().read_text(encoding="utf-8")
        files_block, summary_text = parse_blocks(raw)
        html_text = "\n".join(f["content"] for f in files_block if f["path"].endswith(".html"))
        replay_gaps = marker_gaps(request, html_text)
        ok, errors = write_design(worktree, out_dir, files_block)
        result = {
            "ok": False,
            "dir": out_dir,
            "files": [f["path"] for f in files_block],
            "gaps": replay_gaps,
            "screens": [],
            "handoff": [],
            "shots": [],
            "commit": None,
            "usage": {"model": "replay:" + str(args.from_raw), "prompt_tokens": 0, "completion_tokens": 0},
        }
        if not ok:
            print("\n".join(errors), file=sys.stderr)
            print(json.dumps(result, ensure_ascii=False))
            return 1
        subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, text=True)
        commit = subprocess.run(
            [
                "git",
                "-c",
                "user.name=cascade-designer",
                "-c",
                "user.email=cascade@localhost",
                "commit",
                "-m",
                "docs(design): " + (summary_text.splitlines() or ["replay"])[0][:60],
            ],
            cwd=worktree,
            capture_output=True,
            text=True,
        )
        if commit.returncode == 0:
            result["commit"] = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True
            ).stdout.strip()
            result["ok"] = True
        else:
            result["error"] = commit.stderr.strip()[:300]
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0 if result["ok"] else 1

    user_message = (
        f"INTERFACE TASK FROM THE OWNER\n{request.strip()}\n\n"
        f"DELIVERABLE DIRECTORY (every path of yours starts here): {out_dir}/\n\n"
        f"DESIGN TOKENS, THEME AND INTERFACE RULES OF THIS REPOSITORY\n{design_context(worktree)}\n\n"
        f"REPOSITORY MAP\n{repo_map(worktree)}\n"
    )
    (run_dir / "prompt.md").write_text(user_message, encoding="utf-8")

    gaps: list[str] = []
    answer: dict = {}
    usage: dict = {}
    for attempt in range(1, args.attempts + 1):
        message = user_message
        if gaps:
            message += (
                "\n\nTHE PREVIOUS ATTEMPT WAS REJECTED BY THE ARTEFACT CHECK\n"
                + "\n".join(f"- {gap}" for gap in gaps)
                + "\nFix exactly these points and keep everything else."
            )
        try:
            raw, usage = call_model_stream(cfg, SYSTEM_PROMPT, message, idle_timeout=420)
        except SystemExit as exc:
            (run_dir / "record.json").write_text(
                json.dumps(
                    {"started": stamp, "state": f"failed: {exc}", "model": cfg["model"]}, ensure_ascii=False, indent=1
                ),
                encoding="utf-8",
            )
            raise
        # A model can stop on its own in the middle of a long HTML file (finish_reason=stop, not
        # length). Continue from the exact character instead of paying for a full regeneration.
        for _ in range(4):
            if is_complete(raw):
                break
            tail = raw[-3000:]
            continuation = (
                "You were writing the answer and stopped in the middle of a file. These are the last "
                "characters you wrote:\n<<<TAIL>>>\n" + tail + "\n<<<END TAIL>>>\n"
                "Continue from exactly that point. Output only the remaining text, starting with the "
                "next character. Finish the file and close it with <<<END>>>. No preamble, no apology, "
                "no repetition of anything that came before."
            )
            try:
                more, more_usage = call_model_stream(cfg, SYSTEM_PROMPT, continuation, idle_timeout=420)
            except SystemExit as exc:
                print(f"note: continuation stopped: {exc}", file=sys.stderr)
                break
            raw += more
            for key_name in ("prompt_tokens", "completion_tokens"):
                usage = {**(usage or {})}
                usage[key_name] = int(usage.get(key_name) or 0) + int(more_usage.get(key_name) or 0)
        (run_dir / f"raw-{attempt}.txt").write_text(raw, encoding="utf-8")
        finish = (usage or {}).get("_finish_reason")
        if finish == "length" and not is_complete(raw):
            (run_dir / "record.json").write_text(
                json.dumps(
                    {"started": stamp, "state": "truncated: finish_reason=length", "model": cfg["model"]},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            raise SystemExit("ERROR: the answer hit max_tokens; ask for fewer screens per call")
        if not is_complete(raw):
            (run_dir / "record.json").write_text(
                json.dumps(
                    {"started": stamp, "state": "truncated: a file block has no end marker", "model": cfg["model"]},
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            raise SystemExit(
                "ERROR: the answer stopped inside a file block (output was cut); ask for fewer files per call"
            )
        files_block, summary_text = parse_blocks(raw)
        if files_block:
            answer = {
                "files": files_block,
                "summary": summary_text,
                "title": (summary_text.splitlines() or ["design"])[0][:120],
                "screens": [],
                "handoff": [],
            }
        else:
            answer = extract_json(raw)
        html = "\n".join(
            str(f.get("content") or "") for f in answer.get("files") or [] if str(f.get("path", "")).endswith(".html")
        )
        gaps = marker_gaps(request, html)
        if not gaps:
            break

    result = {
        "ok": False,
        "dir": out_dir,
        "files": [],
        "gaps": gaps,
        "screens": answer.get("screens") or [],
        "handoff": answer.get("handoff") or [],
        "shots": [],
        "commit": None,
        "usage": {
            "model": cfg["model"],
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
        },
    }

    if args.dry_run:
        result["ok"] = True
        print(json.dumps({**result, "answer": answer}, ensure_ascii=False, indent=1))
        return 0

    ok, errors = write_design(worktree, out_dir, answer.get("files") or [])
    if not ok:
        print("\n".join(errors), file=sys.stderr)
        (run_dir / "record.json").write_text(
            json.dumps(
                {"started": stamp, "state": "rejected: " + "; ".join(errors), "model": cfg["model"]},
                ensure_ascii=False,
                indent=1,
            ),
            encoding="utf-8",
        )
        record_usage(slug, out_dir, cfg["model"], usage)
        print(json.dumps(result, ensure_ascii=False))
        return 1

    html_files = [f["path"] for f in answer["files"] if str(f.get("path", "")).endswith(".html")]
    result["files"] = [f["path"] for f in answer["files"]]
    if args.screenshots:
        result["shots"] = render(worktree, html_files)

    subprocess.run(["git", "add", "-A"], cwd=worktree, capture_output=True, text=True)
    commit = subprocess.run(
        [
            "git",
            "-c",
            "user.name=cascade-designer",
            "-c",
            "user.email=cascade@localhost",
            "commit",
            "-m",
            f"docs(design): {answer.get('title') or out_dir}",
        ],
        cwd=worktree,
        capture_output=True,
        text=True,
    )
    if commit.returncode == 0:
        result["commit"] = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=worktree, capture_output=True, text=True
        ).stdout.strip()
        result["ok"] = True
    else:
        result["error"] = commit.stderr.strip()[:400]

    record_usage(slug, out_dir, cfg["model"], usage)
    (run_dir / "record.json").write_text(
        json.dumps(
            {"started": stamp, "state": "done" if result["ok"] else "failed", "model": cfg["model"], "result": result},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(json.dumps(result, ensure_ascii=False, indent=1))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
