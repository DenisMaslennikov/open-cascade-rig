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

import time

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from tools.wrapper.rail_errors import UnknownProjectError, UpstreamError, install_error_handlers
from tools.wrapper.rail_state import CardView, SessionRow, rail_payload
from tools.wrapper.rail_static import _resolve_inside, mount_static

__all__ = ["CardsSource", "RailRequest", "SessionsSource", "UnknownProjectError", "create_app"]

CardsSource = Callable[[str], Sequence[CardView]]
SessionsSource = Callable[[int], Sequence[SessionRow]]

SESSION_LIMIT = 50


class RailRequest(BaseModel):
    """The body of `POST /api/rail`; a body that does not fit it is a validation error, answered 400."""

    project: str = Field(min_length=1)
    seen: dict[str, float] = Field(default_factory=dict)
    lang: str = Field(default="en")


def create_app(
    cards: CardsSource,
    sessions: SessionsSource,
    static_dir: Path,
) -> FastAPI:
    """Build the rail's FastAPI application around the injected sources and static directory.

    The application carries `POST /api/rail`, `GET /api/health` and the static route. Static paths
    are resolved against `static_dir` and a path that resolves outside it answers `404`.

    Arguments:
        cards: Answers the cards of a project slug, newest first, and raises `UnknownProjectError`
            for a slug it does not serve.
        sessions: Answers the recent sessions, newest first, when given a row limit.
        static_dir: Directory holding `index.html` and the rail's assets.

    Returns:
        The configured application, ready to be served or handed to a test client.

    Raises:
        Nothing directly: failures of the sources become `UpstreamError`, answered `502` by the
        handlers `install_error_handlers` installs, and an unknown project answers `400`.
    """
    app = FastAPI(title="Hermes wrapper rail", docs_url=None, redoc_url=None)
    install_error_handlers(app)
    mount_static(app, static_dir)

    @app.get("/api/health")
    def health() -> dict[str, bool]:
        """Answer the ready marker the wrapper polls."""
        return {"ok": True}

    @app.post("/api/rail")
    def rail(request: RailRequest) -> dict[str, Any]:
        """Answer the rail's cards, sessions and counters for one project."""
        try:
            project_cards = cards(request.project)
        except UnknownProjectError:
            raise
        except Exception as error:  # noqa: BLE001 - the mapping belongs to the error handlers
            raise UpstreamError("board", str(error)) from error
        try:
            project_sessions = sessions(SESSION_LIMIT)
        except Exception as error:  # noqa: BLE001 - the mapping belongs to the error handlers
            raise UpstreamError("dashboard", str(error)) from error
        return rail_payload(project_cards, project_sessions, request.seen, time.time(), request.lang)

    @app.get("/{file_path:path}", include_in_schema=False)
    def artefact(file_path: str) -> FileResponse:
        """Serve a static artefact by its path; a path that leaves the directory answers `404`."""
        name = file_path or "index.html"
        target = _resolve_inside(static_dir.resolve(), name)
        if target is None:
            raise HTTPException(status_code=404, detail="Not Found")
        return FileResponse(target)

    return app
