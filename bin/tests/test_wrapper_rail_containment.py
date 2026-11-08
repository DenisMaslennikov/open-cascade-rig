"""Resolved-path containment for the rail's static files through its ASGI application.

A file inside the served directory must be returned with its content type. A lexical traversal and a
symlink that points outside the directory must both receive the same not-found answer.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tools.wrapper.rail_server import create_app
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow


def no_cards(project: str) -> Sequence[CardView]:
    """No cards: this module is about static containment."""
    return []


def no_sessions(limit: int) -> Sequence[SessionRow]:
    """No sessions: this module is about static containment."""
    return []


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    """A client whose static directory holds an asset and a symlink that escapes it."""
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("<!doctype html><title>rail</title>", encoding="utf-8")
    (static / "app.js").write_text("console.log('rail');", encoding="utf-8")
    secret = tmp_path / "owner-secret.txt"
    secret.write_text("the owner's private file", encoding="utf-8")
    (static / "escape.html").symlink_to(secret)

    with TestClient(create_app(no_cards, no_sessions, static)) as started:
        yield started


def test_a_file_inside_the_static_directory_is_served_with_its_content_type(client: TestClient) -> None:
    response = client.get("/app.js")

    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"].lower()
    assert b"console.log" in response.content


@pytest.mark.parametrize(
    "path",
    [
        "/..%2Fowner-secret.txt",
        "/%2e%2e/owner-secret.txt",
        "/assets/..%2F..%2Fowner-secret.txt",
    ],
)
def test_a_traversal_path_gets_the_not_found_answer(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 404
    assert b"private file" not in response.content


def test_a_symlink_that_escapes_the_static_directory_gets_the_not_found_answer(client: TestClient) -> None:
    response = client.get("/escape.html")

    assert response.status_code == 404
    assert b"private file" not in response.content


def test_a_traversal_and_a_symlink_escape_get_the_same_not_found_answer(client: TestClient) -> None:
    symlink_response = client.get("/escape.html")
    traversal_response = client.get("/..%2Fowner-secret.txt")

    assert traversal_response.status_code == symlink_response.status_code == 404
    assert traversal_response.content == symlink_response.content
