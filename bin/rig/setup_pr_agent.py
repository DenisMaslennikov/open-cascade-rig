#!/usr/bin/env python3
"""setup_pr_agent.py — put the pr-agent reviewer into a virtual environment the operator names.

The reviewer used to live in a scratch cache that is pruned after a day of idleness, and the review
phase disappeared with it. The replacement lives outside any cache, and this tool is what puts it
there: it creates the virtual environment, installs pr-agent into it and verifies that the binary
answers. A binary that already works is reported and nothing is created or rewritten, so the tool is
idempotent.

CLI:
  setup_pr_agent.py --venv PATH [--proxy URL] [--pr-agent SPEC] [--json]

`--venv` is required. `--proxy` defaults to $CASCADE_PROXY when that variable is set and to a direct
connection otherwise. `--pr-agent` is the pip specification to install; the default is the package
name. `--json` prints the result as one JSON object instead of a human line.

Output (stdout, with --json): {"status": "present"|"installed", "binary": path, "version": version}

Exit codes: 0 — the binary is in place and answers; 1 — the setup failed, the reason is on stderr;
2 — usage error.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from .rig_conf import PROXY_ENV

DEFAULT_PACKAGE = "pr-agent"
PROBE_TIMEOUT = 120


def probe(binary: Path) -> str | None:
    """The version the installed pr-agent prints, or None when it is missing or does not answer."""
    if not binary.exists():
        return None
    try:
        out = subprocess.run([str(binary), "--version"], capture_output=True, text=True, timeout=PROBE_TIMEOUT)
    except (OSError, subprocess.TimeoutExpired):
        return None
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    if out.returncode != 0 or not lines:
        return None
    return lines[-1]


def install(venv: Path, spec: str, proxy_url: str | None) -> None:
    """Create `venv` and pip-install `spec` into it; SystemExit with the failing step's output."""
    steps = (
        [sys.executable, "-m", "venv", str(venv)],
        [
            str(venv / "bin" / "python"),
            "-m",
            "pip",
            "install",
            *(["--proxy", proxy_url] if proxy_url else []),
            spec,
        ],
    )
    for cmd in steps:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout).strip()[-500:]
            raise SystemExit(f"ERROR: {' '.join(cmd[:3])} failed (exit {proc.returncode}): {detail}")


def report(state: str, binary: Path, version: str, as_json: bool) -> int:
    payload = {"status": state, "binary": str(binary.resolve()), "version": version}
    if as_json:
        print(json.dumps(payload, ensure_ascii=False))
    else:
        print(f"{state}: {binary} ({version})")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a virtual environment with the pr-agent reviewer in it.")
    parser.add_argument("--venv", required=True, help="path of the virtual environment to create or reuse")
    parser.add_argument("--proxy", help=f"proxy URL for the install (default: ${PROXY_ENV} when it is set)")
    parser.add_argument("--pr-agent", help=f"pip specification of the reviewer (default: {DEFAULT_PACKAGE})")
    parser.add_argument("--json", action="store_true", help="print the result as one JSON object")
    args = parser.parse_args()

    binary = Path(args.venv).expanduser() / "bin" / "pr-agent"
    version = probe(binary)
    if version is not None:
        return report("present", binary, version, args.json)

    # A proxy that is not configured means a direct connection, never a guessed one (rig_conf).
    proxy_url = args.proxy or os.environ.get(PROXY_ENV)
    install(Path(args.venv).expanduser(), args.pr_agent or DEFAULT_PACKAGE, proxy_url)
    version = probe(binary)
    if version is None:
        raise SystemExit(f"ERROR: {binary} does not answer after the install of {args.pr_agent or DEFAULT_PACKAGE}")
    return report("installed", binary, version, args.json)


if __name__ == "__main__":
    raise SystemExit(main())
