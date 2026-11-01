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

import json
import threading
from collections.abc import Callable, Sequence
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from typing import Any

from tools.wrapper.rail_state import CardView, rail_payload
from tools.wrapper.sessions_store import SessionRow as SessionRowType

CardsSource = Callable[[str], Sequence[CardView]]
SessionsSource = Callable[[int], Sequence[SessionRowType]]


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
        self._cards_source = cards_source
        self._sessions_source = sessions_source
        self._static_dir = static_dir
        self._host = host
        self._port = port
        self._server = None
        self._thread = None
        self._url = None

    def start(self) -> None:
        """Bind the socket and serve requests in a background thread.

        The call returns once the socket is bound and the thread is serving, so a request issued
        right after it is answered. A port the operating system cannot give raises `OSError`.

        Raises:
            OSError: The address cannot be bound.
        """
        self._server = _RailHTTPServer(
            (self._host, self._port),
            self._cards_source,
            self._sessions_source,
            self._static_dir,
        )
        self._port = self._server.server_address[1]  # Get actual port if 0 was given
        self._url = f"http://{self._host}:{self._port}"

        def serve():
            self._server.serve_forever()

        self._thread = threading.Thread(target=serve, daemon=True)
        self._thread.start()

    @property
    def url(self) -> str:
        """The base URL the server answers on, for example `http://127.0.0.1:8080`.

        When the server was started on port `0` this reports the port the operating system assigned.
        """
        return self._url

    def stop(self) -> None:
        """Stop serving, close the socket and wait for the background thread to finish."""
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        if self._thread:
            self._thread.join()


class _RailHTTPRequestHandler(SimpleHTTPRequestHandler):
    """HTTP request handler for the rail server."""

    def __init__(
        self,
        cards_source: CardsSource,
        sessions_source: SessionsSource,
        static_dir: Path,
        *args,
        **kwargs,
    ):
        self._cards_source = cards_source
        self._sessions_source = sessions_source
        self._static_dir = static_dir
        super().__init__(*args, directory=str(static_dir), **kwargs)

    def do_POST(self) -> None:
        """Handle POST requests to /api/rail."""
        if self.path == "/api/rail":
            self._handle_rail_post()
        else:
            self.send_response(404)
            self.end_headers()

    def do_GET(self) -> None:
        """Handle GET requests."""
        if self.path == "/api/health":
            self._handle_health_get()
        elif self.path == "/":
            # Serve index.html from static directory
            self.path = "/index.html"
            super().do_GET()
        else:
            # Handle static file serving
            # Check if path is valid and safe
            if self.path.startswith("/"):
                path = self.path[1:]
            else:
                path = self.path

            # Prevent directory traversal attacks
            if ".." in path or path.startswith("/") or path.startswith("\\"):
                self.send_response(404)
                self.end_headers()
                return

            file_path = self._static_dir / path
            # Check if file path is within static directory after resolving symlinks
            try:
                resolved_file_path = file_path.resolve()
                resolved_static_dir = self._static_dir.resolve()
                if not resolved_file_path.is_file() or not resolved_file_path.is_relative_to(resolved_static_dir):
                    self.send_response(404)
                    self.end_headers()
                    return
            except (OSError, ValueError):
                # If we can't resolve the path, deny access
                self.send_response(404)
                self.end_headers()
                return

            # Continue with normal file serving
            super().do_GET()

    def _handle_rail_post(self) -> None:
        """Handle POST to /api/rail endpoint."""
        content_length = int(self.headers.get("Content-Length", 0))
        if content_length == 0:
            self._send_error(400, "Empty request body")
            return

        try:
            post_data = self.rfile.read(content_length)
            payload = json.loads(post_data)
        except json.JSONDecodeError:
            self._send_error(400, "Invalid JSON in request body")
            return

        # Validate payload structure
        if not isinstance(payload, dict):
            self._send_error(400, "Request body must be a JSON object")
            return

        project = payload.get("project")
        if project is None:
            self._send_error(400, "Missing 'project' field")
            return

        seen = payload.get("seen")
        if seen is None:
            self._send_error(400, "Missing 'seen' field")
            return

        # Validate seen field
        if not isinstance(seen, dict):
            self._send_error(400, "'seen' must be a dictionary")
            return

        for key, value in seen.items():
            if not isinstance(key, str):
                self._send_error(400, "'seen' keys must be strings")
                return
            if value is not None and not isinstance(value, (int, float)):
                self._send_error(400, "'seen' values must be numbers or null")
                return
            # Reject null values (this is what the test expects)
            if value is None:
                self._send_error(400, "'seen' values must be numbers, not null")
                return

        lang = payload.get("lang")
        if lang is None:
            self._send_error(400, "Missing 'lang' field")
            return

        if lang not in ("en", "ru"):
            self._send_error(400, f"Unsupported language: {lang}")
            return

        try:
            cards = self._cards_source(project)
        except UnknownProjectError:
            self._send_error(400, f"No project {project!r}")
            return
        except Exception as e:
            self._send_bad_gateway("board", str(e))
            return

        try:
            sessions = self._sessions_source(50)  # Use reasonable limit
        except Exception as e:
            self._send_bad_gateway("dashboard", str(e))
            return

        # Build the response
        try:
            import time

            response_data = rail_payload(
                cards=cards,
                sessions=sessions,
                seen=seen,
                now=time.time(),  # Use actual current time
                lang=lang,
            )
        except ValueError as e:
            self._send_error(400, str(e))
            return

        self._send_json_response(200, response_data)

    def _handle_health_get(self) -> None:
        """Handle GET to /api/health endpoint."""
        self._send_json_response(200, {"ok": True})

    def _send_json_response(self, status_code: int, data: dict[str, Any]) -> None:
        """Send a JSON response."""
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def _send_error(self, status_code: int, message: str) -> None:
        """Send an error response."""
        self._send_json_response(status_code, {"error": message})

    def _send_bad_gateway(self, upstream: str, message: str) -> None:
        """Send a 502 Bad Gateway error."""
        self._send_json_response(502, {"error": message, "upstream": upstream})


class _RailHTTPServer(HTTPServer):
    """HTTP server for the rail server."""

    def __init__(
        self,
        server_address,
        cards_source: CardsSource,
        sessions_source: SessionsSource,
        static_dir: Path,
    ):
        self.cards_source = cards_source
        self.sessions_source = sessions_source
        self.static_dir = static_dir
        super().__init__(
            server_address,
            lambda *args, **kwargs: _RailHTTPRequestHandler(cards_source, sessions_source, static_dir, *args, **kwargs),
        )
