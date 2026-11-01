"""Composition layer of the wrapper rail: the two sources, the server and the process entry point.

`rail_server` serves the rail and owns the socket, but it never reads the board or the session
database itself: it asks the two sources it is given. This module is the one place that knows how to
build those sources out of the real objects and how the rail process is started:

- `cards_source` turns a `VikunjaClient` into the card source of the server,
- `sessions_source` turns a `SessionsStore` into the session source,
- `build_server` wires both into a `RailServer` that is not started yet,
- `main` is the process entry point: it parses the address and the paths, builds the server, prints
  the URL it bound to and serves until the process is interrupted.

Nothing here guesses an address, a port or a path: every one of them arrives from the caller or from
the command line, and a missing one is an error, never a default.

`tools/` and `tools/wrapper/` are namespace packages: modules refer to each other by their absolute
path (`tools.wrapper.<module>`) with the repository root on `sys.path`.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

from tools.wrapper.rail_server import RailServer, UnknownProjectError
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow, SessionsStore, SessionsStoreError
from tools.wrapper.vikunja_client import UnknownProject, VikunjaClient, VikunjaError


def cards_source(client: VikunjaClient) -> Callable[[str], Sequence[CardView]]:
    """Build the server's card source out of a board client.

    The returned callable asks `client.plan_cards(project)` and maps every `Card` to a `CardView`:
    `labels` become `(label.title, label.colour)` pairs in board order, `blocked` is the card's own
    property and `url` is passed through unchanged. The board's order is kept.

    An `UnknownProject` from the client is raised again as the server's `UnknownProjectError`, so a
    slug the board does not have is answered with `400`; every other `VikunjaError` travels on
    untouched, so the server answers `502` with `"upstream": "board"` for it.

    Arguments:
        client: The read-only board client the rail reads its cards through.

    Returns:
        A callable that answers the cards of one project slug, in board order.

    Raises:
        UnknownProjectError: Raised by the returned callable for a project the board does not have.
        VikunjaError: Raised by the returned callable when the board itself fails.
    """

    def source(project: str) -> Sequence[CardView]:
        try:
            cards = client.plan_cards(project)
        except UnknownProject as error:
            raise UnknownProjectError(str(error)) from error
        except VikunjaError:
            raise

        return [
            CardView(
                id=card.id,
                title=card.title,
                labels=tuple((label.title, label.colour) for label in card.labels),
                blocked=card.blocked,
                url=card.url,
            )
            for card in cards
        ]

    return source


def sessions_source(store: SessionsStore) -> Callable[[int], Sequence[SessionRow]]:
    """Build the server's session source out of a session store.

    The returned callable asks `store.active(limit)` and passes the rows through unchanged, in the
    order and with the shape the store answered them.

    Arguments:
        store: The read-only session store the rail reads its sessions from.

    Returns:
        A callable that answers at most `limit` recent sessions, newest activity first.

    Raises:
        SessionsStoreError: Raised by the returned callable when the store cannot be read.
    """

    def source(limit: int) -> Sequence[SessionRow]:
        return store.active(limit)

    return source


def build_server(
    *,
    host: str,
    port: int,
    static_dir: Path,
    db_path: Path,
    client: VikunjaClient,
) -> RailServer:
    """Build the rail server out of the objects the process was given.

    The two sources are built here — `cards_source(client)` and
    `sessions_source(SessionsStore(db_path))` — and handed to `RailServer` together with `static_dir`,
    `host` and `port`. The server is returned as `RailServer` constructed it: nothing is bound and
    nothing is served.

    The preflight belongs to the build, so a rail is never started half-working: `static_dir` must be
    an existing directory and `db_path` must be an existing file.

    Arguments:
        host: Interface to bind, for example `127.0.0.1`.
        port: Port to bind; `0` lets the operating system choose one.
        static_dir: Directory holding `index.html` and the rail's assets.
        db_path: Path of the Hermes session store the dashboard writes.
        client: The read-only board client the cards are read through.

    Returns:
        A `RailServer` that is ready to `start()` and has not been started yet.

    Raises:
        FileNotFoundError: `static_dir` is not an existing directory; the message names it.
        SessionsStoreError: `db_path` is not an existing file; the message names it.
    """
    # Validate static_dir exists
    if not static_dir.exists() or not static_dir.is_dir():
        raise FileNotFoundError(f"static directory not found: {static_dir}")

    # Validate db_path exists
    if not db_path.exists() or not db_path.is_file():
        raise SessionsStoreError(f"session store not found: {db_path}")

    # Create the sessions store
    store = SessionsStore(db_path)

    # Build the sources
    cards_src = cards_source(client)
    sessions_src = sessions_source(store)

    # Create and return the server
    return RailServer(
        cards_source=cards_src,
        sessions_source=sessions_src,
        static_dir=static_dir,
        host=host,
        port=port,
    )


def main(argv: list[str] | None = None) -> int:
    """Start the rail: parse the address and the paths, build the server and serve until interrupted.

    Every option is required and has no default: `--host`, `--port` (an integer; `0` is legal and
    binds a port the operating system chooses), `--static-dir` (a directory) and `--db` (the session
    store). The Vikunja side is configured the way `VikunjaClient` configures itself — the
    environment or `~/.config/vikunja/creds.json` — so there is no credential flag here.

    The server is started, the URL it bound to (`server.url`, so the port the operating system chose
    when `--port 0` was given) is printed on one line, and the process then blocks in
    `_wait_for_interrupt` until the operator interrupts it; the server is stopped on the way out.

    A rail that cannot be built is reported and not started: one `ERROR: <message>` line on stderr
    and a return of `1`, with nothing bound and no URL printed. A missing or malformed option is a
    usage error that names the option and leaves `argparse` with exit code `2`, before anything is
    built.

    Arguments:
        argv: The command line without the program name; `sys.argv[1:]` when None.

    Returns:
        `0` after the server was interrupted, `1` when the rail could not be built.

    Raises:
        SystemExit: A required option is missing or malformed; `argparse` reports it and exits `2`.
    """
    parser = argparse.ArgumentParser(description="Run the wrapper rail server")
    parser.add_argument("--host", required=True, help="Host to bind to")
    parser.add_argument("--port", required=True, help="Port to bind to")
    parser.add_argument("--static-dir", required=True, help="Directory with static files")
    parser.add_argument("--db", required=True, help="Path to session store database")

    args = parser.parse_args(argv)

    # Parse port as integer
    try:
        port = int(args.port)
    except ValueError:
        parser.error("--port must be an integer")

    # Validate static directory
    static_dir = Path(args.static_dir)
    if not static_dir.exists() or not static_dir.is_dir():
        print(f"ERROR: static directory not found: {static_dir}", file=sys.stderr)
        return 1

    # Validate database path
    db_path = Path(args.db)
    if not db_path.exists() or not db_path.is_file():
        print(f"ERROR: session store not found: {db_path}", file=sys.stderr)
        return 1

    # Build the client (will use environment/config)
    try:
        client = VikunjaClient()
    except Exception as e:
        print(f"ERROR: board client configuration failed: {e}", file=sys.stderr)
        return 1

    # Build the server
    try:
        server = build_server(
            host=args.host,
            port=port,
            static_dir=static_dir,
            db_path=db_path,
            client=client,
        )
    except Exception as e:
        print(f"ERROR: cannot build rail: {e}", file=sys.stderr)
        return 1

    # Start the server
    try:
        server.start()
    except Exception as e:
        print(f"ERROR: cannot start rail: {e}", file=sys.stderr)
        return 1

    # Print the URL
    print(server.url)

    # Wait for interrupt
    _wait_for_interrupt()

    # Stop the server
    server.stop()

    return 0


def _wait_for_interrupt() -> None:
    """Block the calling thread until the process is interrupted, then return normally.

    `main` waits here between printing the URL and stopping the server. The block ends when the
    process receives `SIGINT` (Ctrl+C) or `SIGTERM`: the `KeyboardInterrupt` is swallowed here, so the caller
    keeps the exit path of an ordinary return. It is a function of its own so a test can drive
    `main` through an injected server without a real interrupt.
    """
    try:
        import signal
        import threading

        # Event to signal shutdown
        shutdown_event = threading.Event()

        # Set up signal handlers to trigger shutdown
        def signal_handler(sig, frame):
            shutdown_event.set()

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        # Sleep until shutdown event is triggered
        while not shutdown_event.is_set():
            shutdown_event.wait(timeout=1.0)
    except KeyboardInterrupt:
        # Allow KeyboardInterrupt to propagate to let the main thread handle cleanup
        pass


if __name__ == "__main__":
    raise SystemExit(main())
