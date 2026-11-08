"""Frozen unit tests for mounting the rail's static assets.

The directory under test is the tests' own folder, so the cases need no server
and write nothing to disk.
"""

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, strategies as st

from rail_static import mount_static

STATIC_DIR = Path(__file__).resolve().parent
ASSET = Path("fixtures") / "vikunja_task_with_blockers.json"


def make_app() -> FastAPI:
    """Return an app whose only job is to mount the tests' asset directory.

    Returns:
        A FastAPI app with the static directory mounted.
    """
    app = FastAPI()
    mount_static(app, STATIC_DIR)
    return app


def test_a_static_asset_is_served_from_the_mounted_directory() -> None:
    with TestClient(make_app()) as client:
        response = client.get(f"/static/{ASSET.as_posix()}")
    assert response.status_code == 200
    assert response.json() == json.loads((STATIC_DIR / ASSET).read_text(encoding="utf-8"))


def test_an_asset_that_is_not_in_the_directory_is_a_404() -> None:
    with TestClient(make_app()) as client:
        response = client.get("/static/no-such-asset.html")
    assert response.status_code == 404


@given(name=st.from_regex(r"missing-[a-z0-9]{1,10}\.txt", fullmatch=True))
def test_an_unknown_asset_name_never_falls_back_to_the_index(name: str) -> None:
    app = FastAPI()
    mount_static(app, STATIC_DIR)
    with TestClient(app) as client:
        response = client.get(f"/static/{name}")
    assert response.status_code == 404
