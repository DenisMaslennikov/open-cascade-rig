"""Composition layer of the wrapper rail: the application factory and the process entry point.

The rail server module owns the routes and the socket, but it never reads the board, the session
database or the seen store itself: it asks the sources it is given. This module is the one place that
knows how to build those sources out of the real objects and how the rail process is started with
uvicorn.

Nothing here guesses an address, a port or a path: every one of them arrives from the caller or from
the command line, and a missing one is an error, never a default.

`tools/` and `tools/wrapper/` are namespace packages: modules refer to each other by their absolute
path (`tools.wrapper.<module>`) with the repository root on `sys.path`.
"""

from __future__ import annotations

import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import uvicorn
from fastapi import FastAPI

from tools.wrapper.rail_db import SeenStore, apply_pending_migrations, session_factory
from tools.wrapper.rail_server import create_app
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow, SessionsStore
from tools.wrapper.vikunja_client import VikunjaClient

__all__ = [
    "SeenStore",
    "SessionsStore",
    "VikunjaClient",
    "apply_pending_migrations",
    "build_app",
    "create_app",
    "main",
    "session_factory",
    "uvicorn",
]


def build_app(
    db_path: Path,
    artefacts_dir: Path,
    board_source: Callable[[str], Sequence[CardView]],
    dashboard_source: Callable[[int], Sequence[SessionRow]],
) -> FastAPI:
    """Build the rail's FastAPI application out of the real sources.

    Binds the rail database session factory and the seen store to the sources the process was given
    and delegates to `rail_server.create_app`, so the returned application answers the routes of the
    server module.

    Arguments:
        db_path: Path of the rail database the seen store is opened on.
        artefacts_dir: Directory holding the dashboard artefacts and the rail's static assets.
        board_source: Callable answering the cards of one project slug.
        dashboard_source: Callable answering at most `limit` recent sessions.

    Returns:
        The FastAPI application, ready to be served.

    Raises:
        FileNotFoundError: `artefacts_dir` is not an existing directory.
        OSError: The rail database cannot be opened.
    """
    raise NotImplementedError


def main(argv: list[str]) -> int:
    """Run the rail process: apply pending migrations, then serve with uvicorn.

    The `--db` option is required and has no default; when it is absent argparse prints the usage and
    an instruction naming the option. Pending migrations are applied before the server starts, and
    uvicorn is the only server launch: it owns the socket and the shutdown, so SIGINT and SIGTERM stop
    the process and this function returns 0.

    Arguments:
        argv: Command-line arguments without the program name.

    Returns:
        The process exit code; 0 after a clean shutdown.

    Raises:
        SystemExit: `--db` is absent or another command-line argument is invalid.
    """
    raise NotImplementedError


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
