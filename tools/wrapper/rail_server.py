"""FastAPI surface of the wrapper rail: one static page and two JSON endpoints.

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

import json
import mimetypes
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ValidationError

from tools.wrapper.rail_state import CardView, rail_payload
from tools.wrapper.sessions_store import SessionRow as SessionRowType


CardsSource = Callable[[str], Sequence[CardView]]
SessionsSource = Callable[[int], Sequence[SessionRowType]]
SeenStore = Callable[[Mapping[str, float]], Mapping[str, float]]


class UnknownProjectError(LookupError):
    """Raised by a `cards_source` for a project slug it does not serve; the server answers 400."""


class RailSources(BaseModel):
    """The injected sources for the rail server.

    Attributes:
        cards_source: Answers the cards of a project slug. It raises `UnknownProjectError` for a
            slug it does not serve and any other exception when the board itself fails.
        sessions_source: Answers the recent sessions, newest first, when given a row limit.
        seen_store: Manages the seen stamps for the rail.
    """

    cards_source: CardsSource
    sessions_source: SessionsSource
    seen_store: SeenStore


def create_app(
    cards_source: CardsSource,
    sessions_source: SessionsSource,
    static_dir: Path,
) -> FastAPI:
    """Create a FastAPI application for the rail server.

    Arguments:
        cards_source: Answers the cards of a project slug.
        sessions_source: Answers the recent sessions, newest first, when given a row limit.
        static_dir: Directory holding `index.html` and the rail's assets.

    Returns:
        A FastAPI application with the rail endpoints and static file serving.
    """
    # Wrap the sources into a RailSources object
    sources = RailSources(
        cards_source=cards_source,
        sessions_source=sessions_source,
        seen_store=lambda seen: seen  # Default implementation - passthrough
    )

    app = FastAPI()

    # Validate static directory exists
    if not static_dir.exists() or not static_dir.is_dir():
        raise FileNotFoundError(f"static directory not found: {static_dir}")

    @app.post("/api/rail")
    async def rail_endpoint(request: Request) -> Response:
        """Handle POST requests to /api/rail."""
        try:
            # Parse JSON body
            body = await request.json()
        except json.JSONDecodeError:
            return JSONResponse(
                status_code=400,
                content={"error": "Invalid JSON in request body"}
            )

        # Validate payload structure
        if not isinstance(body, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "Request body must be a JSON object"}
            )

        project = body.get("project")
        if project is None:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'project' field"}
            )

        seen = body.get("seen")
        if seen is None:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'seen' field"}
            )

        # Validate seen field
        if not isinstance(seen, dict):
            return JSONResponse(
                status_code=400,
                content={"error": "'seen' must be a dictionary"}
            )

        for key, value in seen.items():
            if not isinstance(key, str):
                return JSONResponse(
                    status_code=400,
                    content={"error": "'seen' keys must be strings"}
                )
            if value is not None and not isinstance(value, (int, float)):
                return JSONResponse(
                    status_code=400,
                    content={"error": "'seen' values must be numbers or null"}
                )
            # Reject null values (this is what the test expects)
            if value is None:
                return JSONResponse(
                    status_code=400,
                    content={"error": "'seen' values must be numbers, not null"}
                )

        lang = body.get("lang")
        if lang is None:
            return JSONResponse(
                status_code=400,
                content={"error": "Missing 'lang' field"}
            )

        if lang not in ("en", "ru"):
            return JSONResponse(
                status_code=400,
                content={"error": f"Unsupported language: {lang}"}
            )

        try:
            cards = sources.cards_source(project)
        except UnknownProjectError:
            return JSONResponse(
                status_code=400,
                content={"error": f"No project {project!r}"}
            )
        except Exception as e:
            return JSONResponse(
                status_code=502,
                content={"error": str(e), "upstream": "board"}
            )

        try:
            sessions = sources.sessions_source(50)  # Use reasonable limit
        except Exception as e:
            return JSONResponse(
                status_code=502,
                content={"error": str(e), "upstream": "dashboard"}
            )

        # Apply seen store
        try:
            seen_stamps = sources.seen_store(seen)
        except Exception as e:
            return JSONResponse(
                status_code=502,
                content={"error": str(e), "upstream": "seen-store"}
            )

        # Build the response
        try:
            import time

            response_data = rail_payload(
                cards=cards,
                sessions=sessions,
                seen=seen_stamps,
                now=time.time(),  # Use actual current time
                lang=lang,
            )
        except ValueError as e:
            return JSONResponse(
                status_code=400,
                content={"error": str(e)}
            )

        return JSONResponse(status_code=200, content=response_data)

    @app.get("/api/health")
    async def health_endpoint() -> Response:
        """Handle GET requests to /api/health."""
        return JSONResponse(status_code=200, content={"ok": True})

    @app.get("/")
    async def serve_index() -> Response:
        """Serve the index.html file from the static directory."""
        index_path = static_dir / "index.html"
        if not index_path.exists():
            return JSONResponse(status_code=404, content={"error": "Not found"})
        return FileResponse(index_path)

    @app.get("/{file_path:path}")
    async def serve_static(file_path: str) -> Response:
        """Serve static files from the static directory with containment checks."""
        # Handle empty file path (should be index.html)
        if not file_path:
            return await serve_index()

        # Prevent directory traversal attacks
        if ".." in file_path or file_path.startswith("/") or file_path.startswith("\\"):
            return JSONResponse(status_code=404, content={"error": "Not found"})

        # Construct the file path
        file_path_obj = static_dir / file_path
        
        # Check if file path is within static directory after resolving symlinks
        try:
            resolved_file_path = file_path_obj.resolve()
            resolved_static_dir = static_dir.resolve()
            
            # Check if file exists and is within static directory
            if not resolved_file_path.is_file() or not resolved_file_path.is_relative_to(resolved_static_dir):
                return JSONResponse(status_code=404, content={"error": "Not found"})
        except (OSError, ValueError):
            # If we can't resolve the path, deny access
            return JSONResponse(status_code=404, content={"error": "Not found"})

        # Determine content type
        content_type, _ = mimetypes.guess_type(str(file_path_obj))
        if content_type is None:
            content_type = "application/octet-stream"

        return FileResponse(file_path_obj, media_type=content_type)

    return app
