"""Pin the composition and the command line of the rail application wrapper."""

from __future__ import annotations

import os
import signal
import socket
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import rail_app


WRAPPER_DIR = Path(rail_app.__file__).resolve().parent


class FakeBoardSource:
    """A board source that never touches the network."""

    def fetch(self) -> dict:
        """Return an empty board."""
        return {}


class FakeDashboardSource:
    """A dashboard source that never touches the network."""

    def fetch(self) -> dict:
        """Return an empty dashboard."""
        return {}


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a path to a SQLite database inside a temporary directory."""
    return tmp_path / "rail.sqlite3"


@pytest.fixture
def fake_sources() -> tuple[FakeBoardSource, FakeDashboardSource]:
    """Return fake board and dashboard sources."""
    return FakeBoardSource(), FakeDashboardSource()


def _free_port() -> int:
    """Return a local TCP port that is currently free."""
    with closing(socket.socket(socket.AF_INET, socket.SOCK_STREAM)) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _wait_for_port(port: int, timeout: float = 10.0) -> None:
    """Block until a connection to the local port succeeds."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.05)
    raise TimeoutError(f"server did not listen on port {port}")


def _launch_server(db_path: Path, port: int) -> subprocess.Popen[str]:
    """Launch the rail application with fake sources on the given port."""
    script = f"""
import sys
import rail_app

class FakeBoard:
    def fetch(self):
        return {{}}

class FakeDashboard:
    def fetch(self):
        return {{}}

rail_app.build_board_source = lambda: FakeBoard()
rail_app.build_dashboard_source = lambda: FakeDashboard()
sys.exit(rail_app.main(["--db", {str(db_path)!r}, "--host", "127.0.0.1", "--port", {port!r}]))
"""
    env = os.environ.copy()
    env["PYTHONPATH"] = str(WRAPPER_DIR) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", script],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=env,
    )


def test_database_option_is_required_and_absence_names_it() -> None:
    """The CLI refuses to start without --db and says that --db is required."""
    cmd = [
        sys.executable,
        str(Path(rail_app.__file__)),
        "--host",
        "127.0.0.1",
        "--port",
        "0",
    ]
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=10,
    )
    output = result.stdout + result.stderr
    assert result.returncode != 0
    assert "--db" in output
    assert "required" in output.lower()


def test_startup_applies_migrations_so_seen_table_exists(
    db_path: Path,
    fake_sources: tuple[FakeBoardSource, FakeDashboardSource],
) -> None:
    """Starting the application on an empty directory creates the seen table."""
    board, dashboard = fake_sources
    app = rail_app.create_app(db_path, board, dashboard)
    with TestClient(app):
        pass
    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert "seen" in tables


def test_marks_sent_to_rail_api_survive_restart(
    db_path: Path,
    fake_sources: tuple[FakeBoardSource, FakeDashboardSource],
) -> None:
    """Marks posted to /api/rail are still present after a restart on the same file."""
    board, dashboard = fake_sources
    first_app = rail_app.create_app(db_path, board, dashboard)
    with TestClient(first_app) as client:
        response = client.post("/api/rail", json={"marks": ["alpha", "beta"]})
        assert response.status_code == 200
        response = client.get("/api/rail")
        assert response.status_code == 200
        assert set(response.json()["marks"]) == {"alpha", "beta"}

    second_app = rail_app.create_app(db_path, board, dashboard)
    with TestClient(second_app) as client:
        response = client.get("/api/rail")
        assert response.status_code == 200
        assert set(response.json()["marks"]) == {"alpha", "beta"}
        response = client.post("/api/rail", json={"marks": ["gamma"]})
        assert response.status_code == 200
        response = client.get("/api/rail")
        assert response.status_code == 200
        assert set(response.json()["marks"]) == {"alpha", "beta", "gamma"}


@pytest.mark.parametrize(
    "signal_number",
    [signal.SIGINT, signal.SIGTERM],
    ids=["SIGINT", "SIGTERM"],
)
def test_signal_ends_launched_server_with_exit_code_zero(
    db_path: Path,
    signal_number: signal.Signals,
) -> None:
    """Sending SIGINT or SIGTERM to the uvicorn server exits with code 0."""
    port = _free_port()
    process = _launch_server(db_path, port)
    try:
        _wait_for_port(port)
        process.send_signal(signal_number)
        exit_code = process.wait(timeout=10)
        output = process.stdout.read() if process.stdout else ""
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    assert exit_code == 0
    assert "Uvicorn running on" in output
