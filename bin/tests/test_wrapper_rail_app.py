"""Pin the composition and the command line of the rail application wrapper."""

from __future__ import annotations

import signal
import sqlite3
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

import pytest
import uvicorn
from fastapi.testclient import TestClient

import rail_app


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


class FakeUvicornState:
    """Collect the server double and the stop handlers installed during a test."""

    def __init__(self) -> None:
        """Start with no server, no recorded handler and a server that stops immediately."""
        self.servers: list[object] = []
        self.handlers: dict[int, Callable[[int, object], None]] = {}
        self.on_run: Callable[[object], None] = lambda server: None


class ComposedApp:
    """Stand in for the application main composes, and remember what built it."""

    def __init__(self) -> None:
        """Start with no recorded build call."""
        self.calls: list[tuple[tuple[object, ...], dict[str, object]]] = []


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    """Return a path to a SQLite database inside a temporary directory."""
    return tmp_path / "rail.sqlite3"


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """Return an empty directory standing in for the static assets."""
    path = tmp_path / "static"
    path.mkdir()
    return path


@pytest.fixture
def fake_sources() -> tuple[FakeBoardSource, FakeDashboardSource]:
    """Return fake board and dashboard sources."""
    return FakeBoardSource(), FakeDashboardSource()


@pytest.fixture
def fake_uvicorn(monkeypatch: pytest.MonkeyPatch) -> FakeUvicornState:
    """Replace uvicorn and the signal registry with doubles that record their calls."""
    state = FakeUvicornState()

    class FakeConfig:
        """A uvicorn configuration double that keeps the application and the options."""

        def __init__(self, app: object, **options: object) -> None:
            self.app = app
            self.options = options

    class FakeServer:
        """A uvicorn server double that reports its run and can be asked to stop."""

        def __init__(self, config: FakeConfig) -> None:
            self.config = config
            self.should_exit = False
            self.ran = False
            state.servers.append(self)

        def run(self) -> None:
            self.ran = True
            state.on_run(self)

    module = SimpleNamespace(Config=FakeConfig, Server=FakeServer)
    monkeypatch.setattr(rail_app, "uvicorn", module, raising=False)
    monkeypatch.setattr(uvicorn, "Config", FakeConfig)
    monkeypatch.setattr(uvicorn, "Server", FakeServer)
    monkeypatch.setattr(
        signal,
        "signal",
        lambda number, handler: state.handlers.__setitem__(number, handler),
    )
    return state


@pytest.fixture
def composed_app(monkeypatch: pytest.MonkeyPatch) -> ComposedApp:
    """Replace the real composition so main only has to wire the server."""
    record = ComposedApp()

    def fake_build_app(*args: object, **kwargs: object) -> ComposedApp:
        record.calls.append((args, kwargs))
        return record

    monkeypatch.setattr(rail_app, "build_app", fake_build_app)
    return record


def _compose(
    db_path: Path,
    static_dir: Path,
    sources: tuple[FakeBoardSource, FakeDashboardSource],
) -> object:
    """Build the application from the fake sources and the temporary database."""
    board, dashboard = sources
    return rail_app.build_app(
        board_source=board,
        dashboard_source=dashboard,
        static_dir=static_dir,
        db_path=db_path,
    )


def test_database_option_is_required_and_the_error_names_it(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A missing --db stops the command line before startup and the message names --db."""
    with pytest.raises(SystemExit) as exit_info:
        rail_app.main(["--host", "127.0.0.1", "--port", "8000"])

    assert exit_info.value.code != 0
    message = capsys.readouterr().err
    assert "--db" in message
    assert "required" in message.lower()


def test_startup_applies_pending_migrations_to_an_empty_data_directory(
    db_path: Path,
    static_dir: Path,
    fake_sources: tuple[FakeBoardSource, FakeDashboardSource],
) -> None:
    """Starting on a data directory that does not exist yet creates the seen table."""
    app = _compose(db_path, static_dir, fake_sources)

    with TestClient(app):
        pass

    with sqlite3.connect(db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }

    assert "seen" in tables


def test_marks_posted_to_the_rail_survive_a_restart_on_the_same_file(
    db_path: Path,
    static_dir: Path,
    fake_sources: tuple[FakeBoardSource, FakeDashboardSource],
) -> None:
    """Marks posted to /api/rail are still served after the application is rebuilt on the same file."""
    first_app = _compose(db_path, static_dir, fake_sources)
    with TestClient(first_app) as client:
        response = client.post("/api/rail", json={"marks": ["alpha", "beta"]})
        assert response.status_code == 200
        response = client.get("/api/rail")
    assert response.status_code == 200
    assert set(response.json()["marks"]) == {"alpha", "beta"}

    second_app = _compose(db_path, static_dir, fake_sources)
    with TestClient(second_app) as client:
        response = client.get("/api/rail")

    assert response.status_code == 200
    assert set(response.json()["marks"]) == {"alpha", "beta"}


def test_uvicorn_is_handed_the_composed_application_and_run(
    fake_uvicorn: FakeUvicornState,
    composed_app: ComposedApp,
    db_path: Path,
) -> None:
    """Main builds the application, hands it to uvicorn, runs it and returns zero."""
    exit_code = rail_app.main(["--db", str(db_path)])

    assert exit_code == 0
    assert len(fake_uvicorn.servers) == 1
    server = fake_uvicorn.servers[0]
    assert server.config.app is composed_app
    assert server.ran is True

    args, kwargs = composed_app.calls[0]
    assert db_path in [*args, *kwargs.values()]


@pytest.mark.parametrize(
    "stop_signal",
    [signal.SIGINT, signal.SIGTERM],
    ids=["sigint", "sigterm"],
)
def test_stop_signal_asks_the_server_to_exit_and_the_process_returns_zero(
    stop_signal: signal.Signals,
    fake_uvicorn: FakeUvicornState,
    composed_app: ComposedApp,
    db_path: Path,
) -> None:
    """Delivering SIGINT or SIGTERM to the running server stops it and the process exits zero."""

    def deliver(server: object) -> None:
        assert stop_signal in fake_uvicorn.handlers
        fake_uvicorn.handlers[stop_signal](stop_signal, None)

    fake_uvicorn.on_run = deliver

    exit_code = rail_app.main(["--db", str(db_path)])

    assert exit_code == 0
    assert fake_uvicorn.servers[0].should_exit is True
