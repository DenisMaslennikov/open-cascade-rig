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


def main(argv: list[str] | None = None) -> int:
    """List the tools of the package, or dispatch to one of them.

    Arguments:
        argv: the arguments after `-m rig`, defaulting to `sys.argv[1:]`.

    Returns:
        0 after `--list`, the exit code of the dispatched tool, or 2 when the tool is unknown.

    Raises:
        NotImplementedError: until the dispatcher is implemented.
    """
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main())
