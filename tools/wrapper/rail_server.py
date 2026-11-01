"""HTTP surface of the wrapper rail: one static page and two JSON endpoints.

The server owns the socket and nothing else. Cards and sessions arrive through the two sources the
caller injects, so the rail can be started in a test without a board and without a session
database, and the server never reads either of them itself.

Endpoints:

- `POST /api/rail` takes `{"project": "...", "seen": {"<session id>": <epoch seconds>}, "lang":
  "en" | "ru"}` and answers `200` with the body `rail_payload` builds. A request that cannot be
  answered as asked (missing or unknown project, body that is not a JSON object, unknown language,
  malformed `seen` map) answers `400` with `{"error": "..."}`; an upstream that fails answers `502`
  with `{"error": "<the upstream's own text>", "upstream": "board" | "dashboard"}`.
- `GET /api/health` answers `200 {"ok": true}`.
- `GET /` serves `static_dir/index.html`, `GET /<file>` serves that file of `static_dir`; anything
  else is `404`, including a path that escapes `static_dir` and a directory (no listings).

`tools/` and `tools/wrapper/` are namespace packages: modules refer to each other by their absolute
path (`tools.wrapper.<module>`) with the repository root on `sys.path`.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow

CardsSource = Callable[[str], Sequence[CardView]]
SessionsSource = Callable[[int], Sequence[SessionRow]]


class UnknownProjectError(LookupError):
    """Raised by a `cards_source` for a project slug it does not serve; the server answers 400."""


class RailServer:
    """Serves the rail's page and its two JSON endpoints on a background thread.

    Arguments:
        cards_source: Answers the cards of a project slug. It raises `UnknownProjectError` for a
            slug it does not serve and any other exception when the board itself fails.
        sessions_source: Answers the recent sessions, newest first, when given a row limit.
        static_dir: Directory holding `index.html` and the rail's assets.
        host: Interface to bind, for example `127.0.0.1`.
        port: Port to bind; `0` lets the operating system choose one.
    """

    def __init__(
        self,
        cards_source: CardsSource,
        sessions_source: SessionsSource,
        static_dir: Path,
        host: str,
        port: int,
    ) -> None:
        """Store the sources and the address; nothing is bound or served here.

        Arguments:
            cards_source: Answers the cards of a project slug.
            sessions_source: Answers the recent sessions when given a row limit.
            static_dir: Directory holding `index.html` and the rail's assets.
            host: Interface to bind.
            port: Port to bind; `0` lets the operating system choose one.
        """
        raise NotImplementedError

    def start(self) -> None:
        """Bind the socket and serve requests in a background thread.

        The call returns once the socket is bound and the thread is serving, so a request issued
        right after it is answered. A port the operating system cannot give raises `OSError`.

        Raises:
            OSError: The address cannot be bound.
        """
        raise NotImplementedError

    @property
    def url(self) -> str:
        """The base URL the server answers on, for example `http://127.0.0.1:8080`.

        When the server was started on port `0` this reports the port the operating system assigned.
        """
        raise NotImplementedError

    def stop(self) -> None:
        """Stop serving, close the socket and wait for the background thread to finish."""
        raise NotImplementedError
