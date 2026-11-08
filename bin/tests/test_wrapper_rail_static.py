"""Behaviour of the static file mount with path containment."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from hypothesis import given, settings, strategies as st

from tools.wrapper.rail_static import mount_static


def _client(directory: Path) -> TestClient:
    app = FastAPI()
    mount_static(app, directory)
    return TestClient(app)


@pytest.fixture
def static_site(tmp_path: Path) -> tuple[Path, Path]:
    site = tmp_path / "site"
    site.mkdir()
    (site / "index.html").write_bytes(b"<html>index</html>")
    nested = site / "nested"
    nested.mkdir()
    (nested / "page.txt").write_bytes(b"nested page")
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside secret")
    return site, outside


def test_serves_index_html_with_its_bytes(static_site: tuple[Path, Path]) -> None:
    site, _ = static_site
    response = _client(site).get("/static/index.html")
    assert response.status_code == 200
    assert response.content == b"<html>index</html>"


def test_serves_nested_file_with_its_bytes(static_site: tuple[Path, Path]) -> None:
    site, _ = static_site
    response = _client(site).get("/static/nested/page.txt")
    assert response.status_code == 200
    assert response.content == b"nested page"


@pytest.mark.parametrize(
    "request_path",
    [
        "/static/../outside.txt",
        "/static/%2e%2e%2foutside.txt",
        "/static/..%2foutside.txt",
    ],
)
def test_traversal_outside_directory_returns_404(
    static_site: tuple[Path, Path], request_path: str
) -> None:
    site, _ = static_site
    response = _client(site).get(request_path)
    assert response.status_code == 404
    assert response.content != b"outside secret"


def test_symlink_pointing_outside_directory_returns_404(static_site: tuple[Path, Path]) -> None:
    site, outside = static_site
    link = site / "leak.txt"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("symbolic links are not supported on this platform")
    response = _client(site).get("/static/leak.txt")
    assert response.status_code == 404
    assert response.content != b"outside secret"


def test_missing_file_returns_404(static_site: tuple[Path, Path]) -> None:
    site, _ = static_site
    response = _client(site).get("/static/missing.txt")
    assert response.status_code == 404


@given(
    first=st.from_regex(r"[a-z][a-z0-9]{0,8}", fullmatch=True),
    second=st.from_regex(r"[a-z][a-z0-9]{0,8}\.txt", fullmatch=True),
)
@settings(max_examples=10, deadline=None)
def test_generated_path_inside_directory_is_served(
    tmp_path_factory: pytest.TempPathFactory, first: str, second: str
) -> None:
    site = tmp_path_factory.mktemp("site")
    nested = site / first
    nested.mkdir(parents=True, exist_ok=True)
    payload = b"payload"
    (nested / second).write_bytes(payload)
    response = _client(site).get(f"/static/{first}/{second}")
    assert response.status_code == 200
    assert response.content == payload
