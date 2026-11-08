"""Behaviour contract for the rail's HTTP surface: the page, its assets and the two JSON endpoints.

The tests talk to a real loopback socket, because the contract itself is an exchange of HTTP messages:
the status, the body and the content type are what the rail's front end sees. The board and the
session store are injected, so nothing here needs either of them.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from collections.abc import Callable, Iterator, Sequence
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest

from tools.wrapper.rail_server import RailServer, UnknownProjectError
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow

HOST = "127.0.0.1"

CARDS = [
    CardView(id=184, title="Rail", labels=(("ui", "#5b8ff9"),), blocked=False, url="http://board.test/tasks/184"),
]
SESSIONS = [SessionRow(id="s-1", title="Session one", last_message_at=None, running=True, message_count=3)]

EN_AGE = re.compile(r"now|\d+ (min|h|d)")
RU_AGE = re.compile(r"сейчас|\d+ (мин|ч|д)")


def cards_of(project: str) -> Sequence[CardView]:
    """Answer the one card of the project; `project` is not inspected by these tests."""
    return CARDS


def sessions_of(limit: int) -> Sequence[SessionRow]:
    """Answer the recent sessions, cut to the requested limit."""
    return SESSIONS[:limit]


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A directory shaped like the rail's page: an index, three assets and one nested page."""
    root = tmp_path / "static"
    (root / "assets").mkdir(parents=True)
    (root / "index.html").write_text("<!doctype html><title>Hermes rail</title>", encoding="utf-8")
    (root / "app.css").write_text("body { color: #e6edf7; }", encoding="utf-8")
    (root / "app.js").write_text("console.log('rail');", encoding="utf-8")
    (root / "icon.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"></svg>', encoding="utf-8")
    (root / "assets" / "deep.html").write_text("<p>deep</p>", encoding="utf-8")
    (tmp_path / "outside.txt").write_text("owner secrets", encoding="utf-8")
    return root


@pytest.fixture
def serve(static_dir: Path) -> Iterator[Callable[..., RailServer]]:
    """Start rail servers on port zero and stop every one of them at the end of the test."""
    started: list[RailServer] = []

    def start(
        cards: Callable[[str], Sequence[CardView]] = cards_of,
        sessions: Callable[[int], Sequence[SessionRow]] = sessions_of,
    ) -> RailServer:
        server = RailServer(cards, sessions, static_dir, HOST, 0)
        server.start()
        started.append(server)
        return server

    yield start

    for server in started:
        server.stop()


@pytest.fixture
def server(serve: Callable[..., RailServer]) -> RailServer:
    """A started rail server answering the default sources."""
    return serve()


def fetch(url: str, *, body: bytes | None = None, method: str = "GET") -> tuple[int, dict[str, str], bytes]:
    """Send one request and return its status, its lower-case headers and its body."""
    address = urllib.parse.urlsplit(url)
    connection = HTTPConnection(address.hostname, address.port, timeout=5)
    try:
        connection.request(method, address.path or "/", body=body)
        response = connection.getresponse()
        return (
            response.status,
            {key.lower(): value for key, value in response.getheaders()},
            response.read(),
        )
    finally:
        connection.close()


def post_rail(server: RailServer, payload: object) -> tuple[int, Any]:
    """Post a JSON payload to `/api/rail` and return the status with the decoded answer."""
    status, _, raw = fetch(server.url + "/api/rail", body=json.dumps(payload).encode("utf-8"), method="POST")
    return status, json.loads(raw)


def post_raw(server: RailServer, body: bytes) -> tuple[int, Any]:
    """Post a body that is not JSON and return the status with the decoded answer."""
    status, _, raw = fetch(server.url + "/api/rail", body=body, method="POST")
    return status, json.loads(raw)


def test_the_rail_endpoint_answers_the_cards_the_sessions_and_the_counters(server: RailServer) -> None:
    status, body = post_rail(server, {"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert status == 200
    assert body == {
        "cards": [
            {
                "id": 184,
                "title": "Rail",
                "labels": [["ui", "#5b8ff9"]],
                "blocked": False,
                "url": "http://board.test/tasks/184",
            }
        ],
        "sessions": [
            {
                "id": "s-1",
                "title": "Session one",
                "last_message_at": None,
                "running": True,
                "unread": False,
                "relative": "now",
            }
        ],
        "counters": {"cards": 1, "sessions": 1, "unread": 0},
    }


def test_the_rail_endpoint_marks_a_session_whose_message_is_newer_than_the_seen_stamp(
    serve: Callable[..., RailServer],
) -> None:
    server = serve(
        sessions=lambda limit: [
            SessionRow(id="s-2", title="Newest", last_message_at=100.0, running=False, message_count=1)
        ]
    )

    status, body = post_rail(server, {"project": "cascade-rig", "seen": {"s-2": 50.0}, "lang": "en"})

    assert status == 200
    assert body["sessions"][0]["unread"] is True
    assert body["counters"]["unread"] == 1
    assert EN_AGE.fullmatch(body["sessions"][0]["relative"])


def test_the_rail_endpoint_counts_a_session_seen_after_its_message_as_read(
    serve: Callable[..., RailServer],
) -> None:
    server = serve(
        sessions=lambda limit: [
            SessionRow(id="s-2", title="Newest", last_message_at=100.0, running=False, message_count=1)
        ]
    )

    status, body = post_rail(server, {"project": "cascade-rig", "seen": {"s-2": 500.0}, "lang": "en"})

    assert status == 200
    assert body["sessions"][0]["unread"] is False
    assert body["counters"]["unread"] == 0


def test_the_rail_endpoint_answers_in_russian(server: RailServer) -> None:
    status, body = post_rail(server, {"project": "cascade-rig", "seen": {}, "lang": "ru"})

    assert status == 200
    assert body["sessions"][0]["relative"] == "сейчас"
    assert RU_AGE.fullmatch(body["sessions"][0]["relative"])


def test_the_sources_are_asked_with_the_project_and_a_positive_limit(
    serve: Callable[..., RailServer],
) -> None:
    projects: list[str] = []
    limits: list[int] = []

    def cards(project: str) -> Sequence[CardView]:
        projects.append(project)
        return CARDS

    def sessions(limit: int) -> Sequence[SessionRow]:
        limits.append(limit)
        return SESSIONS

    server = serve(cards=cards, sessions=sessions)

    status, _ = post_rail(server, {"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert status == 200
    assert projects == ["cascade-rig"]
    assert limits and limits[0] > 0


def test_a_missing_project_is_rejected(server: RailServer) -> None:
    status, body = post_rail(server, {"seen": {}, "lang": "en"})

    assert status == 400
    assert isinstance(body["error"], str) and body["error"]


def test_an_unknown_project_is_rejected(serve: Callable[..., RailServer]) -> None:
    def cards(project: str) -> Sequence[CardView]:
        raise UnknownProjectError(f"no project {project!r}")

    server = serve(cards=cards)

    status, body = post_rail(server, {"project": "nope", "seen": {}, "lang": "en"})

    assert status == 400
    assert "nope" in body["error"]


def test_a_body_that_is_not_an_object_is_rejected(server: RailServer) -> None:
    status, body = post_rail(server, ["cascade-rig"])

    assert status == 400
    assert body["error"]


def test_a_body_that_is_not_json_is_rejected(server: RailServer) -> None:
    status, body = post_raw(server, b"project=cascade-rig")

    assert status == 400
    assert body["error"]


def test_an_unknown_language_is_rejected(server: RailServer) -> None:
    status, body = post_rail(server, {"project": "cascade-rig", "seen": {}, "lang": "de"})

    assert status == 400
    assert body["error"]


def test_a_payload_without_a_language_is_rejected(server: RailServer) -> None:
    status, body = post_rail(server, {"project": "cascade-rig", "seen": {}})

    assert status == 400
    assert body["error"]


@pytest.mark.parametrize(
    "seen",
    [["s-1"], None, {"s-1": "yesterday"}, {"s-1": None}],
    ids=["list", "null", "text stamp", "null stamp"],
)
def test_a_malformed_seen_map_is_rejected(server: RailServer, seen: object) -> None:
    status, body = post_rail(server, {"project": "cascade-rig", "seen": seen, "lang": "en"})

    assert status == 400
    assert body["error"]


def test_a_board_failure_is_a_bad_gateway(serve: Callable[..., RailServer]) -> None:
    def cards(project: str) -> Sequence[CardView]:
        raise RuntimeError("the board refused the token")

    server = serve(cards=cards)

    status, body = post_rail(server, {"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert status == 502
    assert body["upstream"] == "board"
    assert "the board refused the token" in body["error"]


def test_a_dashboard_failure_is_a_bad_gateway(serve: Callable[..., RailServer]) -> None:
    def sessions(limit: int) -> Sequence[SessionRow]:
        raise RuntimeError("the state database is locked")

    server = serve(sessions=sessions)

    status, body = post_rail(server, {"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert status == 502
    assert body["upstream"] == "dashboard"
    assert "the state database is locked" in body["error"]


def test_health_reports_ok(server: RailServer) -> None:
    status, _, raw = fetch(server.url + "/api/health")

    assert status == 200
    assert json.loads(raw) == {"ok": True}


def test_the_page_is_served_at_the_root(server: RailServer, static_dir: Path) -> None:
    status, headers, raw = fetch(server.url + "/")

    assert status == 200
    assert raw == (static_dir / "index.html").read_bytes()
    assert headers["content-type"].split(";")[0].strip() == "text/html"


@pytest.mark.parametrize(
    ("path", "allowed"),
    [
        ("/app.css", ("text/css",)),
        ("/app.js", ("text/javascript", "application/javascript")),
        ("/icon.svg", ("image/svg+xml",)),
    ],
)
def test_an_asset_carries_its_content_type(server: RailServer, path: str, allowed: tuple[str, ...]) -> None:
    status, headers, raw = fetch(server.url + path)

    assert status == 200
    assert raw
    assert headers["content-type"].split(";")[0].strip() in allowed


def test_a_file_in_a_subdirectory_of_the_static_directory_is_served(server: RailServer) -> None:
    status, _, raw = fetch(server.url + "/assets/deep.html")

    assert status == 200
    assert raw == b"<p>deep</p>"


@pytest.mark.parametrize(
    "path",
    ["/missing.html", "/../outside.txt", "/%2e%2e/outside.txt", "/assets", "/assets/"],
    ids=["unknown file", "parent", "encoded parent", "directory", "directory with slash"],
)
def test_a_path_that_is_not_a_file_of_the_static_directory_is_not_found(server: RailServer, path: str) -> None:
    status, _, raw = fetch(server.url + path)

    assert status == 404
    assert b"owner secrets" not in raw


def test_a_port_of_zero_reports_the_port_the_operating_system_assigned(
    serve: Callable[..., RailServer],
) -> None:
    server = serve()
    port = urllib.parse.urlparse(server.url).port

    assert server.url == f"http://{HOST}:{port}"
    assert port is not None and port > 0
    status, _, raw = fetch(server.url + "/api/health")
    assert status == 200
    assert json.loads(raw) == {"ok": True}


def test_stopping_the_server_closes_the_socket(static_dir: Path) -> None:
    server = RailServer(cards_of, sessions_of, static_dir, HOST, 0)
    server.start()
    url = server.url
    server.stop()

    with pytest.raises(OSError):
        fetch(url + "/api/health")
