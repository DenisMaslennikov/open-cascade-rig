"""Static-file containment of the rail server: a path outside the served directory is not served.

The frozen suite next door covers lexical escapes (`..`, encoded dots, directories). This file covers
the case those cannot see: a symlink that lives *inside* the static directory and points outside it.
`Path.is_relative_to` is lexical, so it accepts such a path; only a resolved-path check rejects it.
"""

from __future__ import annotations

import urllib.parse
from collections.abc import Iterator
from http.client import HTTPConnection
from pathlib import Path

import pytest

from tools.wrapper.rail_server import RailServer
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow

HOST = "127.0.0.1"


def fetch(url: str) -> tuple[int, bytes]:
    """Send one GET and return its status with the body."""
    address = urllib.parse.urlsplit(url)
    connection = HTTPConnection(address.hostname, address.port, timeout=5)
    try:
        connection.request("GET", address.path or "/")
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


@pytest.fixture
def server(tmp_path: Path) -> Iterator[RailServer]:
    """A rail server whose static directory holds a symlink that escapes it."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>rail</title>", encoding="utf-8")
    (static / "app.js").write_text("console.log('rail');", encoding="utf-8")
    secret = tmp_path / "owner-secret.txt"
    secret.write_text("the owner's private file", encoding="utf-8")
    (static / "escape.html").symlink_to(secret)

    def cards(project: str) -> list[CardView]:
        """No cards: this file is about static containment."""
        return []

    def sessions(limit: int) -> list[SessionRow]:
        """No sessions: this file is about static containment."""
        return []

    started = RailServer(cards, sessions, static, HOST, 0)
    started.start()
    yield started
    started.stop()


def test_a_file_of_the_static_directory_is_served(server: RailServer) -> None:
    status, body = fetch(server.url + "/app.js")

    assert status == 200
    assert b"console.log" in body


def test_a_symlink_that_escapes_the_static_directory_is_not_served(server: RailServer) -> None:
    status, body = fetch(server.url + "/escape.html")

    assert status == 404
    assert b"private file" not in body
