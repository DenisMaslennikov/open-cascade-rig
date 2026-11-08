#!/usr/bin/env python3
"""__main__.py — the stable entry point of the rig: `cd bin && python3 -m rig <tool> [args...]`.

The scripts became a package (card #191), so `~/.config/cascade/bin/<tool>.py` — a symlink to `bin/` —
no longer resolves to a runnable script. This module is the one address that survives the move.

  python3 -m rig --list        every module of the package that exposes a module-level `main`, one
                               name per line, sorted, exit 0
  python3 -m rig <tool> [args] imports `rig.<tool>` and calls its `main` in this process with
                               `sys.argv` set to `["rig <tool>", *args]`, so argparse prints the right
                               program name; the exit code is the tool's own
  an unknown tool              the available names go to stderr, exit 2, no traceback
"""

from __future__ import annotations

import ast
import importlib
import sys
from pathlib import Path


def _tools() -> list[str]:
    """The names of the package's modules that expose a module-level `main`, sorted.

    The package directory is read from this module's own location, not from `dir(rig)`: listing the
    tools must not import every module, so a module with a broken import or a syntax error still
    gets named here instead of taking the listing down with it.
    """
    package = Path(__file__).resolve().parent
    return sorted(path.stem for path in package.glob("*.py") if not path.stem.startswith("_") and callable_tool(path))


def callable_tool(path: Path) -> bool:
    """Whether the module at `path` compiles and defines a module-level `main`."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return False
    return any(isinstance(node, ast.FunctionDef) and node.name == "main" for node in tree.body)


def main(argv: list[str] | None = None) -> int:
    """List the tools of the package, or dispatch to one of them.

    Arguments:
        argv: the arguments after `-m rig`, defaulting to `sys.argv[1:]`.

    Returns:
        0 after `--list`, the exit code of the dispatched tool, or 2 when the tool is unknown.
    """
    args = sys.argv[1:] if argv is None else list(argv)
    if args and args[0] == "--list":
        print("\n".join(_tools()))
        return 0
    if not args:
        print("\n".join(_tools()), file=sys.stderr)
        return 2
    tool, rest = args[0], args[1:]
    if tool not in _tools():
        print(f"unknown tool: {tool}", file=sys.stderr)
        print("available: " + " ".join(_tools()), file=sys.stderr)
        return 2
    module = importlib.import_module(f"rig.{tool}")
    sys.argv = [f"rig {tool}", *rest]
    return module.main()


if __name__ == "__main__":
    raise SystemExit(main())
