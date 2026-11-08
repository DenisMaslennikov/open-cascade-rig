"""`rig.setup_pr_agent` puts the reviewer in a venv the operator names, and installs nothing twice.

Card #207: the reviewer used to live in a Hermes scratch cache that is pruned after a day, and the
review phase disappeared with it. The replacement lives outside any cache, and this tool is what
puts it there: `--venv` is mandatory, an already working binary is reported `present` with the
version it printed, and the JSON names the path that was configured — never one under the cache.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from rig import setup_pr_agent


def _bin_dir() -> Path:
    return Path(setup_pr_agent.__file__).resolve().parent.parent


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "rig.setup_pr_agent", *args],
        cwd=_bin_dir(),
        capture_output=True,
        text=True,
        check=False,
    )


def _fake_venv(root: Path, version: str = "pr-agent 0.0") -> Path:
    binary = root / "venv" / "bin" / "pr-agent"
    binary.parent.mkdir(parents=True)
    binary.write_text(f"#!/bin/sh\necho '{version}'\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _snapshot(root: Path) -> dict[str, int]:
    """Every path under the venv and its mtime, so a rewritten file and a new file both show up."""
    return {str(path.relative_to(root)): path.stat().st_mtime_ns for path in root.rglob("*")}


def test_a_missing_venv_is_a_usage_error_naming_the_option():
    proc = _run("--json")

    assert proc.returncode == 2
    assert "--venv" in proc.stderr
    assert "Traceback" not in proc.stderr


def test_an_existing_reviewer_is_reported_present_and_left_untouched(tmp_path):
    binary = _fake_venv(tmp_path)
    venv = tmp_path / "venv"
    before = _snapshot(venv)

    proc = _run("--venv", str(venv), "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["status"] == "present"
    assert payload["binary"] == str(binary.resolve())
    assert payload["version"].strip() == "pr-agent 0.0"
    assert _snapshot(venv) == before


def test_the_reported_binary_is_the_configured_path_not_a_hermes_cache_path(tmp_path):
    binary = _fake_venv(tmp_path)
    venv = tmp_path / "venv"

    proc = _run("--venv", str(venv), "--json")

    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["binary"] == str(binary.resolve())
    assert ".hermes/cache" not in payload["binary"]
