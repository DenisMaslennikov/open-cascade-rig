"""Behaviour of the error mapping the wrapper rail app installs."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "tools" / "wrapper"))

from rail_errors import UpstreamError, UnknownProjectError, install_error_handlers  # noqa: E402


class _Echo(BaseModel):
    """A body the echo route validates."""

    value: int


def _build_client(unknown_project_message: str = "nope") -> TestClient:
    """Build a bare application carrying the error handlers and one route per failure."""
    app = FastAPI()

    @app.get("/unknown-project")
    def read_unknown_project() -> dict[str, str]:
        raise UnknownProjectError(unknown_project_message)

    @app.get("/board-cards")
    def read_board_cards() -> dict[str, str]:
        raise UpstreamError("board")

    @app.get("/sessions")
    def read_sessions() -> dict[str, str]:
        raise UpstreamError("dashboard")

    @app.post("/echo")
    def echo(body: _Echo) -> dict[str, int]:
        return {"value": body.value}

    install_error_handlers(app)
    return TestClient(app)


def test_unknown_project_answers_400() -> None:
    response = _build_client().get("/unknown-project")

    assert response.status_code == 400


@pytest.mark.parametrize(
    ("path", "upstream"),
    [("/board-cards", "board"), ("/sessions", "dashboard")],
)
def test_failing_upstream_answers_502_naming_the_source(path: str, upstream: str) -> None:
    response = _build_client().get(path)

    assert response.status_code == 502
    assert response.json()["upstream"] == upstream


def test_malformed_json_body_answers_400() -> None:
    response = _build_client().post(
        "/echo",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 400


def test_well_formed_json_body_reaches_the_route() -> None:
    response = _build_client().post("/echo", json={"value": 7})

    assert response.status_code == 200
    assert response.json() == {"value": 7}


@settings(max_examples=25, deadline=None)
@given(message=st.text())
def test_unknown_project_message_never_changes_the_status(message: str) -> None:
    response = _build_client(message).get("/unknown-project")

    assert response.status_code == 400
