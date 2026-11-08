"""HTTP boundary of the rail application built by create_app: routes, statuses, static serving."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from tools.wrapper.rail_server import CardsSource, SessionsSource, UnknownProjectError, create_app

STATIC_DIR = Path(__file__).resolve().parents[2] / "tools" / "wrapper" / "rail" / "design"
SERVED_PROJECT = "occc"


def _cards_serving_one_project(project: str) -> Sequence[Any]:
    if project != SERVED_PROJECT:
        raise UnknownProjectError(project)
    return []


def _cards_that_fail(project: str) -> Sequence[Any]:
    raise RuntimeError("board is down")


def _sessions_answering_nothing(limit: int) -> Sequence[Any]:
    return []


def _sessions_that_fail(limit: int) -> Sequence[Any]:
    raise RuntimeError("dashboard is down")


def _client(
    cards: CardsSource = _cards_serving_one_project,
    sessions: SessionsSource = _sessions_answering_nothing,
    static_dir: Path = STATIC_DIR,
) -> TestClient:
    return TestClient(create_app(cards=cards, sessions=sessions, static_dir=static_dir))


def _rail_body(project: str = SERVED_PROJECT) -> dict[str, Any]:
    return {"project": project, "seen": {}, "lang": "en"}


def test_health_answers_ok_true() -> None:
    response = _client().get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_rail_request_answers_the_payload_of_the_sources() -> None:
    response = _client().post("/api/rail", json=_rail_body())

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert isinstance(response.json(), dict)


@pytest.mark.parametrize(
    "raw_body",
    [b"not json", b"[]", b'{"project": "occc", "seen": [], "lang": "en"}'],
    ids=["invalid-json", "not-an-object", "seen-is-not-a-map"],
)
def test_malformed_body_answers_400(raw_body: bytes) -> None:
    response = _client().post(
        "/api/rail",
        content=raw_body,
        headers={"content-type": "application/json"},
    )

    assert response.status_code == 400


def test_unknown_project_answers_400() -> None:
    response = _client().post("/api/rail", json=_rail_body("nope"))

    assert response.status_code == 400


def test_cards_failure_answers_502_naming_the_board() -> None:
    response = _client(cards=_cards_that_fail).post("/api/rail", json=_rail_body())

    assert response.status_code == 502
    assert response.json()["upstream"] == "board"


def test_sessions_failure_answers_502_naming_the_dashboard() -> None:
    response = _client(sessions=_sessions_that_fail).post("/api/rail", json=_rail_body())

    assert response.status_code == 502
    assert response.json()["upstream"] == "dashboard"


def test_static_file_inside_the_directory_is_served() -> None:
    response = _client().get("/rail.md")

    assert response.status_code == 200
    assert response.content == (STATIC_DIR / "rail.md").read_bytes()


def test_path_escaping_the_static_directory_answers_404() -> None:
    response = _client().get("/../rail_server.py")

    assert response.status_code == 404
