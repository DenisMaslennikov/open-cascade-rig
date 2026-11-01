#!/usr/bin/env python3
"""config_init.py — the canonical config lives in the repository, the live one in ~/.config/cascade.

Two copies of anything drift, and these two drifted within a day: the reviewer prompts were edited in
the live config and the repository kept its import-time version. The split that removes the drift:

  canonical  cascade-rig/config/{prompts,schema}   (versioned, edited by a pull request)
  live       ~/.config/cascade/{prompts,schema}    (symlinks into the repository)
  local only ~/.config/cascade/{keys.env, projects/*.yaml, pricing.json}

`--link` makes the live copies symlinks, so an edit is a commit or it does not exist. `--check` reports
drift without touching anything (the useful default). `--materialize` creates a live project file from
its example the first time.

CLI:
  config_init.py --check [--repo PATH]
  config_init.py --link  [--repo PATH] [--dry-run]
  config_init.py --materialize --project <slug> [--repo PATH] [--force]

Exit 0 — in sync (or the action succeeded), 1 — drift found / action failed.
"""

from __future__ import annotations

import argparse
import filecmp
import os
import shutil
import sys
from pathlib import Path

from rig_conf import RIG_ENV, rig_repo

LIVE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "cascade"


def canonical(repo: Path, name: str) -> Path:
    return repo / "config" / name


def live(name: str) -> Path:
    return LIVE / name


def same(a: Path, b: Path) -> bool:
    if a.is_symlink() or b.is_symlink():
        return a.exists() and b.exists() and a.resolve() == b.resolve()
    if not (a.exists() and b.exists()):
        return False
    if a.is_dir() and b.is_dir():
        names = {p.name for p in a.iterdir()} | {p.name for p in b.iterdir()}
        return all(same(a / n, b / n) for n in names if (a / n).exists() and (b / n).exists())
    return filecmp.cmp(a, b, shallow=False)


def check(repo: Path) -> int:
    rows = []
    drift = 0
    for name in ("prompts", "schema"):
        c, l = canonical(repo, name), live(name)
        state = "in sync" if same(c, l) else ("symlinked" if l.is_symlink() else "drift")
        if state == "drift":
            drift += 1
        rows.append({"item": name, "canonical": str(c), "live": str(l), "state": state})

    roles_live = live("roles.yaml")
    rows.append(
        {
            "item": "roles.yaml",
            "canonical": str(canonical(repo, "roles.example.yaml")),
            "live": str(roles_live),
            "state": "present (local, intentional)" if roles_live.exists() else "missing — run --materialize",
        }
    )

    projects = sorted((LIVE / "projects").glob("*.yaml")) if (LIVE / "projects").exists() else []
    rows.append(
        {
            "item": "projects/*.yaml",
            "canonical": str(canonical(repo, "projects")),
            "live": str(LIVE / "projects"),
            "state": f"{len(projects)} live project file(s): {', '.join(p.stem for p in projects)}",
        }
    )

    keys = LIVE / "keys.env"
    rows.append(
        {
            "item": "keys.env",
            "canonical": "(never in the repository)",
            "live": str(keys),
            "state": "present" if keys.exists() else "missing",
        }
    )

    print("\n".join(f"{row['item']:16} {row['state']:45} {row['live']}" for row in rows))
    if drift:
        print(
            f"\n{drift} item(s) drifted from the repository. Live copies must be symlinks; "
            f"run `config_init.py --link` after moving any local edit into the repository."
        )
    return 1 if drift else 0


def link(repo: Path, dry: bool) -> int:
    failures = 0
    targets = {
        "prompts": (repo / "config" / "prompts", LIVE / "prompts"),
        "schema": (repo / "config" / "schema", LIVE / "schema"),
        "bin": (repo / "bin", LIVE / "bin"),
    }
    for name, (source, target) in targets.items():
        if not source.exists():
            continue
        if target.is_symlink() and target.resolve() == source.resolve():
            print(f"{name:8} already linked -> {source}")
            continue
        if target.exists() and not target.is_symlink():
            # Never silently discard a local edit: the caller has to move it into the repository first.
            if name in ("prompts", "schema") and not same(source, target):
                print(
                    f"{name:8} REFUSED: {target} differs from {source}. "
                    f"Copy the difference into the repository first (a prompt edit is a commit)."
                )
                failures += 1
                continue
            backup = target.with_suffix(target.suffix + ".pre-link.bak")
            print(f"{name:8} {'would back up' if dry else 'backing up'} {target} -> {backup.name}")
            if not dry:
                if target.is_dir():
                    shutil.move(str(target), str(backup))
                else:
                    target.rename(backup)
        if not dry:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.symlink_to(source, target_is_directory=True)
        print(f"{name:8} {'would link' if dry else 'linked'} {target} -> {source}")
    return 1 if failures else 0


def materialize(repo: Path, slug: str, force: bool) -> int:
    source = canonical(repo, f"projects/{slug}.example.yaml")
    target = LIVE / "projects" / f"{slug}.yaml"
    if not source.exists():
        print(f"ERROR: no example at {source}", file=sys.stderr)
        return 1
    if target.exists() and not force:
        print(f"{target} already exists; pass --force to overwrite")
        return 0
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    print(f"materialized {target} from {source}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Keep the live cascade config and the repository in step")
    ap.add_argument("--repo", help=f"the cascade-rig checkout; default: ${RIG_ENV} or the current directory")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--link", action="store_true")
    ap.add_argument("--materialize", action="store_true")
    ap.add_argument("--project", help="slug for --materialize")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    repo = rig_repo(args.repo)
    if not (repo / "config").exists():
        print(f"ERROR: no config directory in {repo}", file=sys.stderr)
        return 1
    if args.materialize:
        if not args.project:
            print("ERROR: --materialize needs --project <slug>", file=sys.stderr)
            return 1
        return materialize(repo, args.project, args.force)
    if args.link:
        return link(repo, args.dry_run)
    return check(repo)


if __name__ == "__main__":
    raise SystemExit(main())
