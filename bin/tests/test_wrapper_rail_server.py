"""Behaviour contract for the rail's JSON API through its ASGI application.

The application is created with fake board and session sources and driven with fastapi.testclient, so
the tests see exactly the status and JSON body the rail's front end receives. They pin the successful
payload, the rejection of bad input and the way a failing source becomes a bad gateway.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from tools.wrapper.rail_server import UnknownProjectError, create_app
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow

CARDS = [
    CardView(id=184, title="Rail", labels=(("ui", "#5b8ff9"),), blocked=False, url="http://board.test/tasks/184"),
]
SESSIONS = [SessionRow(id="s-1", title="Session one", last_message_at=None, running=True, message_count=3)]

EN_AGE = re.compile(r"now|[0-9]+ (min|h|d)")
RU_AGE = re.compile(r"сейчас|[0-9]+ (мин|ч|д)")


def cards_of(project: str) -> Sequence[CardView]:
    """Answer the one card of the project; the project name is not inspected."""
    return CARDS


def sessions_of(limit: int) -> Sequence[SessionRow]:
    """Answer the recent sessions, cut to the requested limit."""
    return SESSIONS[:limit]


@pytest.fixture
def make_client(tmp_path: Path) -> Iterator[Callable[..., TestClient]]:
    """Build ASGI clients over an empty static directory and the given sources."""

    def build(
        cards: Callable[[str], Sequence[CardView]] = cards_of,
        sessions: Callable[[int], Sequence[SessionRow]] = sessions_of,
    ) -> TestClient:
        static = tmp_path / "static"
        static.mkdir(exist_ok=True)
        (static / "index.html").write_text("<!doctype html><title>rail</title>", encoding="utf-8")
        return TestClient(create_app(cards, sessions, static))

    yield build


@pytest.fixture
def client(make_client: Callable[..., TestClient]) -> Iterator[TestClient]:
    """A client over the default fake sources."""
    with make_client() as started:
        yield started


def test_the_rail_endpoint_returns_the_cards_the_sessions_and_the_counters(client: TestClient) -> None:
    response = client.post("/api/rail", json={"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert response.status_code == 200
    assert response.json() == {
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


def test_the_rail_endpoint_preserves_the_order_of_cards_and_sessions(
    make_client: Callable[..., TestClient],
) -> None:
    cards = [
        CardView(id=2, title="Second", labels=(), blocked=True, url="http://board.test/tasks/2"),
        CardView(id=1, title="First", labels=(), blocked=False, url="http://board.test/tasks/1"),
    ]
    sessions = [
        SessionRow(id="s-2", title="Second session", last_message_at=None, running=False, message_count=1),
        SessionRow(id="s-1", title="First session", last_message_at=None, running=True, message_count=2),
    ]

    with make_client(cards=lambda project: cards, sessions=lambda limit: sessions) as started:
        body = started.post("/api/rail", json={"project": "cascade-rig", "seen": {}, "lang": "en"}).json()

    assert [card["id"] for card in body["cards"]] == [2, 1]
    assert [session["id"] for session in body["sessions"]] == ["s-2", "s-1"]


def test_a_session_whose_message_is_newer_than_the_seen_stamp_is_unread(
    make_client: Callable[..., TestClient],
) -> None:
    sessions = [SessionRow(id="s-2", title="Newest", last_message_at=100.0, running=False, message_count=1)]

    with make_client(sessions=lambda limit: sessions) as started:
        body = started.post(
            "/api/rail",
            json={"project": "cascade-rig", "seen": {"s-2": 50.0}, "lang": "en"},
        ).json()

    assert body["sessions"][0]["unread"] is True
    assert body["counters"]["unread"] == 1
    assert EN_AGE.fullmatch(body["sessions"][0]["relative"])


def test_a_session_seen_after_its_message_is_read(make_client: Callable[..., TestClient]) -> None:
    sessions = [SessionRow(id="s-2", title="Newest", last_message_at=100.0, running=False, message_count=1)]

    with make_client(sessions=lambda limit: sessions) as started:
        body = started.post(
            "/api/rail",
            json={"project": "cascade-rig", "seen": {"s-2": 500.0}, "lang": "en"},
        ).json()

    assert body["sessions"][0]["unread"] is False
    assert body["counters"]["unread"] == 0


def test_the_relative_age_is_localised_for_the_requested_language(
    make_client: Callable[..., TestClient],
) -> None:
    sessions = [SessionRow(id="s-3", title="Russian", last_message_at=100.0, running=False, message_count=1)]

    with make_client(sessions=lambda limit: sessions) as started:
        body = started.post(
            "/api/rail",
            json={"project": "cascade-rig", "seen": {}, "lang": "ru"},
        ).json()

    assert RU_AGE.fullmatch(body["sessions"][0]["relative"])


@given(st.one_of(st.integers(), st.text(), st.lists(st.integers())))
def test_a_seen_map_that_is_not_an_object_is_rejected(client: TestClient, broken_seen: object) -> None:
    response = client.post("/api/rail", json={"project": "cascade-rig", "seen": broken_seen, "lang": "en"})

    assert response.status_code == 400


def test_an_unknown_project_is_rejected(make_client: Callable[..., TestClient]) -> None:
    def unknown(project: str) -> Sequence[CardView]:
        raise UnknownProjectError(project)

    with make_client(cards=unknown) as started:
        response = started.post("/api/rail", json={"project": "missing", "seen": {}, "lang": "en"})

    assert response.status_code == 400


def test_a_failing_board_source_becomes_a_bad_gateway_naming_board(
    make_client: Callable[..., TestClient],
) -> None:
    def failing(project: str) -> Sequence[CardView]:
        raise RuntimeError("board is down")

    with make_client(cards=failing) as started:
        response = started.post("/api/rail", json={"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert response.status_code == 502
    assert "board" in response.text.lower()


def test_a_failing_sessions_source_becomes_a_bad_gateway_naming_dashboard(
    make_client: Callable[..., TestClient],
) -> None:
    def failing(limit: int) -> Sequence[SessionRow]:
        raise RuntimeError("dashboard is down")

    with make_client(sessions=failing) as started:
        response = started.post("/api/rail", json={"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert response.status_code == 502
    assert "dashboard" in response.text.lower()


def test_health_reports_the_service_is_ready(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body.get("status") == "ok" or body.get("ok") is True
