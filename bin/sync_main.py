#!/usr/bin/env python3
"""sync_main.py — bring every local main up to origin/main after a pull request is merged.

The repository is the source of truth, and a stale local `main` is how a new branch starts from a base
that no longer exists: the branch then conflicts on a clean merge, and CI catches what a `git pull`
would have caught in a second. This script is that pull, for all the projects at once.

Policy: fast-forward only. `main` is never rebased or merged into locally — if it cannot fast-forward,
something else is wrong (a local commit on main) and a human has to look. A dirty worktree is reported
and skipped, never stashed or reset.

CLI:
  sync_main.py [--repo PATH ...] [--json]

Exit 0 — everything up to date, 1 — something needs a human.
"""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from rig_conf import REPOS_ENV, repos


def git(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)


def sync(repo: Path) -> dict:
    row: dict[str, object] = {"repo": str(repo)}
    if not (repo / ".git").exists():
        row.update({"state": "not a repository"})
        return row
    dirty = git(repo, "status", "--porcelain").stdout.strip()
    fetch = git(repo, "fetch", "origin", "--prune")
    if fetch.returncode != 0:
        row.update({"state": "fetch failed", "error": fetch.stderr.strip()[:200]})
        return row
    before = git(repo, "rev-parse", "HEAD").stdout.strip()
    behind = git(repo, "rev-list", "--count", "HEAD..origin/main").stdout.strip() or "0"
    row["behind"] = int(behind)
    if dirty:
        row.update({"state": "skipped: dirty worktree", "paths": len(dirty.splitlines())})
        return row
    if behind == "0":
        row.update({"state": "up to date", "head": before[:7]})
        return row
    branch = git(repo, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        row.update({"state": f"on {branch}, not main — not synced"})
        return row
    merge = git(repo, "merge", "--ff-only", "origin/main")
    after = git(repo, "rev-parse", "HEAD").stdout.strip()
    row.update(
        {
            "state": "fast-forwarded" if merge.returncode == 0 else "cannot fast-forward",
            "from": before[:7],
            "to": after[:7],
        }
    )
    if merge.returncode != 0:
        row["error"] = merge.stderr.strip()[:200]
    return row


def main() -> int:
    ap = argparse.ArgumentParser(description="Fast-forward every local main to origin/main")
    ap.add_argument("--repo", action="append", help="repository path (repeatable)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    targets = [Path(p).expanduser() for p in args.repo] if args.repo else repos()
    if not targets:
        raise SystemExit(
            f"ERROR: no repositories to sync. Pass --repo PATH, set {REPOS_ENV}, or put "
            f"`repo: <path>` in a project config under ~/.config/cascade/projects/."
        )
    rows = [sync(repo) for repo in targets]
    bad = [r for r in rows if r["state"] not in ("up to date", "fast-forwarded")]
    if args.json:
        print(json.dumps({"repos": rows, "needs_human": bad}, ensure_ascii=False, indent=1))
    else:
        for row in rows:
            print(
                f"{row['repo']:40} {row['state']:26} {row.get('from', '')}{'->' + row['to'] if row.get('to') else ''}"
            )
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
