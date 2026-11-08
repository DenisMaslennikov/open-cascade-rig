"""Frozen unit tests for the wrapper's HTTP routes.

The app is built over in-memory card and session sources, so every case runs
without a database, a network or a running server.
"""

from pathlib import Path

from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from rail_server import create_app

STATIC_DIR = Path(__file__).resolve().parent
SEEN_AT = "2026-01-01T00:00:00+00:00"


class FakeCards(list):
    """In-memory card source holding the card dicts the app renders."""


class FakeSessions(list):
    """In-memory session source holding the session dicts the app renders."""


def make_client(cards: list[dict] | None = None, sessions: list[dict] | None = None) -> TestClient:
    """Return a client over an app wired to the given in-memory sources.

    Arguments:
        cards: card dicts the fake card source holds.
        sessions: session dicts the fake session source holds.

    Returns:
        A test client for the app; use it as a context manager so the lifespan runs.
    """
    return TestClient(create_app(FakeCards(cards or []), FakeSessions(sessions or []), STATIC_DIR))


def test_health_reports_ok() -> None:
    with make_client() as client:
        response = client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["ok"] is True


def test_rail_payload_is_empty_when_no_card_and_no_session_exists() -> None:
    with make_client() as client:
        response = client.post("/api/rail", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["cards"] == []
    assert body["sessions"] == []


def test_a_card_source_reaches_the_rail_payload() -> None:
    cards = [{"id": 7, "title": "Ship the rail", "status": "backlog"}]
    with make_client(cards=cards) as client:
        response = client.post("/api/rail", json={})
    assert response.status_code == 200
    assert [card["id"] for card in response.json()["cards"]] == [7]


def test_a_seen_stamp_for_an_unknown_card_does_not_create_a_card() -> None:
    with make_client() as client:
        response = client.post("/api/rail", json={"seen": {"7": SEEN_AT}})
    assert response.status_code == 200
    assert response.json()["cards"] == []


@given(
    seen=st.dictionaries(
        keys=st.integers(min_value=1, max_value=10**6).map(str),
        values=st.just(SEEN_AT),
        max_size=3,
    )
)
def test_a_seen_stamp_for_any_unknown_card_leaves_the_payload_empty(seen: dict[str, str]) -> None:
    with TestClient(create_app(FakeCards(), FakeSessions(), STATIC_DIR)) as client:
        response = client.post("/api/rail", json={"seen": seen})
    assert response.status_code == 200
    assert response.json()["cards"] == []
