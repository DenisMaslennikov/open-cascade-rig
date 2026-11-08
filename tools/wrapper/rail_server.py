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
from typing import Any

from fastapi import FastAPI

from tools.wrapper.rail_state import CardView

__all__ = ["UnknownProjectError", "create_app"]

CardsSource = Callable[[str], Sequence[CardView]]
SessionsSource = Callable[[int], Sequence[Any]]


class UnknownProjectError(LookupError):
    """Raised by a cards source for a project slug it does not serve; the rail route answers 400."""


def create_app(
    cards: CardsSource,
    sessions: SessionsSource,
    static_dir: Path,
) -> FastAPI:
    """Build the rail's FastAPI application around the injected sources and static directory.

    The application carries `POST /api/rail`, `GET /api/health` and the static route. Static paths
    are resolved against `static_dir` and a path that resolves outside it answers `404`.

    Arguments:
n        cards: Answers the cards of a project slug, newest first, and raises `UnknownProjectError`
            for a slug it does not serve.
        sessions: Answers the recent sessions, newest first, when given a row limit.
        static_dir: Directory holding `index.html` and the rail's assets.

    Returns:
        The configured application, ready to be served or handed to a test client.

    Raises:
        NotImplementedError: Always, until the routes are implemented.
    """
    raise NotImplementedError
