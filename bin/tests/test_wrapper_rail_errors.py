"""Frozen unit tests for the wrapper's error handlers.

A failed upstream becomes a 502 whose body names the upstream, so the page can
say which side is down without reading prose.
"""

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from rail_errors import UpstreamError, install_error_handlers


def make_app(upstream: str) -> FastAPI:
    """Return an app whose only route fails because ``upstream`` is down.

    Arguments:
        upstream: the name the raised upstream error carries.

    Returns:
        A FastAPI app with the error handlers installed and one failing route.
    """
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/probe")
    def probe() -> dict[str, bool]:
        raise UpstreamError(upstream)

    return app


def test_a_board_outage_is_a_502_naming_the_board() -> None:
    with TestClient(make_app("board")) as client:
        response = client.get("/probe")
    assert response.status_code == 502
    assert response.json()["upstream"] == "board"


def test_a_dashboard_outage_is_a_502_naming_the_dashboard() -> None:
    with TestClient(make_app("dashboard")) as client:
        response = client.get("/probe")
    assert response.status_code == 502
    assert response.json()["upstream"] == "dashboard"


def test_a_failure_that_is_not_an_upstream_outage_stays_a_500() -> None:
    app = FastAPI()
    install_error_handlers(app)

    @app.get("/probe")
    def probe() -> dict[str, bool]:
        raise ValueError("boom")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/probe")
    assert response.status_code == 500


@given(upstream=st.sampled_from(["board", "dashboard", "cards", "sessions"]))
def test_every_upstream_failure_names_that_upstream_in_the_body(upstream: str) -> None:
    with TestClient(make_app(upstream)) as client:
        response = client.get("/probe")
    assert response.status_code == 502
    assert response.json()["upstream"] == upstream
