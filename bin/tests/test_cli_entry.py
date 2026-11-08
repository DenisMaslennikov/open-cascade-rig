"""`python3 -m rig` is the one address the move to a package left working.

Card #191 turned the scripts into `bin/rig`, and `~/.config/cascade/bin/<tool>.py` — historically a
symlink into `bin/` — stopped resolving to a runnable script. These tests freeze the replacement: the
listing of the tools, the dispatch that hands the tool its own name in `sys.argv`, the exit code that
comes back, the rejection of an unknown tool, and the check `config_init` runs over a package
directory so a broken module is named instead of raising a traceback on the machine that needed it.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path

from rig import __main__ as dispatcher
from rig import config_init, usage


def _bin_dir() -> Path:
    return Path(config_init.__file__).resolve().parent.parent


def _tools_in_package() -> list[str]:
    """The tool names the package carries, read from the sources rather than from the dispatcher."""
    tools = []
    for path in sorted(_bin_dir().joinpath("rig").glob("*.py")):
        if path.stem.startswith("_"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        if any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body):
            tools.append(path.stem)
    return tools


def test_list_prints_each_tool_that_exposes_main(capsys):
    assert dispatcher.main(["--list"]) == 0

    assert capsys.readouterr().out.split() == _tools_in_package()


def test_a_dispatched_tool_sees_its_own_name_in_argv(monkeypatch):
    seen: list[list[str]] = []

    def fake_main() -> int:
        seen.append(list(sys.argv))
        return 0

    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(usage, "main", fake_main)

    assert dispatcher.main(["usage", "--json", "--project", "demo"]) == 0
    assert seen == [["rig usage", "--json", "--project", "demo"]]


def test_the_tool_exit_code_is_the_dispatcher_exit_code(monkeypatch):
    monkeypatch.setattr(sys, "argv", ["pytest"])
    monkeypatch.setattr(usage, "main", lambda: 3)

    assert dispatcher.main(["usage", "report"]) == 3


def test_an_unknown_tool_names_the_available_ones_on_stderr(capsys):
    assert dispatcher.main(["no-such-tool"]) == 2

    captured = capsys.readouterr()

    assert "usage" in captured.err
    assert "Traceback" not in captured.err


def test_a_module_without_main_is_not_a_tool(capsys):
    assert dispatcher.main(["rig_conf"]) == 2

    assert "gate" in capsys.readouterr().err


def test_the_module_entry_point_runs_the_listing():
    proc = subprocess.run(
        [sys.executable, "-m", "rig", "--list"],
        cwd=_bin_dir(),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 0, proc.stderr
    assert proc.stdout.split() == _tools_in_package()


def test_the_entry_check_names_the_modules_the_dispatcher_cannot_reach(tmp_path):
    package = tmp_path / "bin" / "rig"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "alpha.py").write_text("def main() -> int:\n    return 0\n", encoding="utf-8")
    (package / "beta.py").write_text("VALUE = 1\n", encoding="utf-8")
    (package / "gamma.py").write_text("def broken(:\n", encoding="utf-8")

    report = config_init.entry_report(tmp_path / "bin")

    assert set(report) == {"tools", "unreachable"}
    assert report["tools"] == ["alpha"]
    assert report["unreachable"] == ["beta", "gamma"]
