"""Behaviour contract for the rail's JSON API through its ASGI application.

The application is built over fake board and session sources and driven with fastapi.testclient, so the
tests observe exactly the status and the JSON body the rail's front end receives. They pin the
successful payload, the rejection of bad input and the way a failing source becomes a bad gateway.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from hypothesis import HealthCheck, given, settings, strategies as st

from tools.wrapper.rail_server import UnknownProjectError, create_app
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow

PROJECT = "cascade-rig"

CARDS = [
    CardView(id=184, title="Rail", labels=(("ui", "#5b8ff9"),), blocked=False, url="http://board.test/tasks/184"),
]
SESSIONS = [SessionRow(id="s-1", title="Session one", last_message_at=None, running=True, message_count=3)]


def cards_of(project: str) -> Sequence[CardView]:
    """Answer the one card of the project; the project name is not inspected."""
    return CARDS


def sessions_of(limit: int) -> Sequence[SessionRow]:
    """Answer the recent sessions, cut to the requested limit."""
    return SESSIONS[:limit]


def unknown_project(project: str) -> Sequence[CardView]:
    """Refuse a project the board source does not know, the way the real source does."""
    raise UnknownProjectError(project)


def broken_board(project: str) -> Sequence[CardView]:
    """Fail the board lookup with an error that names no upstream."""
    raise RuntimeError("upstream refused the connection")


def broken_dashboard(limit: int) -> Sequence[SessionRow]:
    """Fail the session lookup with an error that names no upstream."""
    raise RuntimeError("upstream refused the connection")


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
    response = client.post("/api/rail", json={"project": PROJECT, "seen": {}, "lang": "en"})

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
        body = started.post("/api/rail", json={"project": PROJECT, "seen": {}, "lang": "en"}).json()

    assert [card["id"] for card in body["cards"]] == [2, 1]
    assert [session["id"] for session in body["sessions"]] == ["s-2", "s-1"]


def test_a_session_whose_message_is_newer_than_the_seen_stamp_is_unread(
    make_client: Callable[..., TestClient],
) -> None:
    sessions = [SessionRow(id="s-2", title="Newest", last_message_at=100.0, running=False, message_count=1)]

    with make_client(sessions=lambda limit: sessions) as started:
        body = started.post(
            "/api/rail",
            json={"project": PROJECT, "seen": {"s-2": 50.0}, "lang": "en"},
        ).json()

    assert body["sessions"][0]["unread"] is True
    assert body["counters"]["unread"] == 1


def test_a_session_seen_after_its_message_is_read(make_client: Callable[..., TestClient]) -> None:
    sessions = [SessionRow(id="s-2", title="Newest", last_message_at=100.0, running=False, message_count=1)]

    with make_client(sessions=lambda limit: sessions) as started:
        body = started.post(
            "/api/rail",
            json={"project": PROJECT, "seen": {"s-2": 500.0}, "lang": "en"},
        ).json()

    assert body["sessions"][0]["unread"] is False
    assert body["counters"]["unread"] == 0


def test_the_relative_age_is_localised_for_the_requested_language(
    make_client: Callable[..., TestClient],
) -> None:
    sessions = [SessionRow(id="s-3", title="Russian", last_message_at=None, running=False, message_count=1)]

    with make_client(sessions=lambda limit: sessions) as started:
        body = started.post("/api/rail", json={"project": PROJECT, "seen": {}, "lang": "ru"}).json()

    assert body["sessions"][0]["relative"] == "сейчас"


def test_the_health_endpoint_reports_the_application_is_up(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_an_unknown_project_is_rejected_with_a_bad_request(make_client: Callable[..., TestClient]) -> None:
    with make_client(cards=unknown_project) as started:
        response = started.post("/api/rail", json={"project": "no-such-project", "seen": {}, "lang": "en"})

    assert response.status_code == 400


def test_a_failing_board_source_becomes_a_bad_gateway_naming_board(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client(cards=broken_board) as started:
        response = started.post("/api/rail", json={"project": PROJECT, "seen": {}, "lang": "en"})

    assert response.status_code == 502
    assert response.json()["upstream"] == "board"


def test_a_failing_session_source_becomes_a_bad_gateway_naming_dashboard(
    make_client: Callable[..., TestClient],
) -> None:
    with make_client(sessions=broken_dashboard) as started:
        response = started.post("/api/rail", json={"project": PROJECT, "seen": {}, "lang": "en"})

    assert response.status_code == 502
    assert response.json()["upstream"] == "dashboard"


@given(
    seen=st.dictionaries(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=6),
        st.one_of(
            st.none(),
            st.text(alphabet="abcdefghijklmnopqrstuvwxyz -", min_size=1, max_size=10),
            st.lists(st.integers()),
        ),
        min_size=1,
        max_size=3,
    )
)
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_a_seen_map_whose_stamps_are_not_numbers_is_rejected(
    make_client: Callable[..., TestClient], seen: dict[str, object]
) -> None:
    with make_client() as started:
        response = started.post("/api/rail", json={"project": PROJECT, "seen": seen, "lang": "en"})

    assert response.status_code == 400
