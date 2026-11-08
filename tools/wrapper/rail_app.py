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

import argparse
import signal
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
    artefacts = Path(artefacts_dir)
    if not artefacts.is_dir():
        raise FileNotFoundError(f"the artefacts directory does not exist: {artefacts}")
    make_session = session_factory(Path(db_path))
    SeenStore(make_session)
    return create_app(board_source, dashboard_source, artefacts)


def _missing_source(message: str) -> Callable[..., Sequence[object]]:
    """A source that refuses with an instruction, used when its option was not given.

    Arguments:
        message: The instruction the caller must read; it names the missing option.

    Returns:
        A callable raising `RuntimeError` with that message, which the rail answers as `502`.
    """

    def refuse(*args: object, **kwargs: object) -> Sequence[object]:
        raise RuntimeError(message)

    return refuse


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
    parser = argparse.ArgumentParser(
        prog="rail_app",
        description="Serve the Hermes wrapper rail: the board's cards and the live chat sessions.",
    )
    parser.add_argument("--db", required=True, help="path of the rail's own SQLite database")
    parser.add_argument(
        "--artefacts",
        default=str(Path(__file__).resolve().parent / "rail" / "design"),
        help="directory holding the rail's static artefacts",
    )
    parser.add_argument("--board-url", default=None, help="base URL of the Vikunja board API")
    parser.add_argument("--dashboard-db", default=None, help="path of the Hermes session database")
    parser.add_argument("--host", default="127.0.0.1", help="address uvicorn binds to")
    parser.add_argument("--port", type=int, default=8765, help="port uvicorn binds to")
    args = parser.parse_args(argv)

    db_path = Path(args.db)
    apply_pending_migrations(db_path)
    if args.board_url:
        cards: Callable[[str], Sequence[CardView]] = VikunjaClient(args.board_url).plan_cards
    else:
        print("WARNING: no --board-url: the board source answers 502 until one is given", file=sys.stderr)
        cards = _missing_source("--board-url is required to read the board")
    if args.dashboard_db:
        sessions: Callable[[int], Sequence[SessionRow]] = SessionsStore(Path(args.dashboard_db)).active
    else:
        print("WARNING: no --dashboard-db: the dashboard source answers 502 until one is given", file=sys.stderr)
        sessions = _missing_source("--dashboard-db is required to read the sessions")
    application = build_app(db_path, Path(args.artefacts), cards, sessions)

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        uvicorn.run(application, host=args.host, port=args.port)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
