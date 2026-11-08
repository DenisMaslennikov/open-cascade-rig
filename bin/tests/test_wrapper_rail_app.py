"""Behaviour contract for the rail's composition layer: the two sources, the builder and the CLI.

The rail is proved here the way it is started in production: a board client and a session store go
in, a `RailServer` comes out, the process prints the URL it bound to and serves. The board is a fake
transport of the real `VikunjaClient`, so the card mapping is exercised over real card objects, and
the store is a real SQLite file built by the test, so nothing here needs a board, a live dashboard
or a network.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from collections.abc import Sequence
from http.client import HTTPConnection
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.wrapper import rail_app
from tools.wrapper.rail_server import RailServer, UnknownProjectError
from tools.wrapper.rail_state import CardView
from tools.wrapper.sessions_store import SessionRow, SessionsStore, SessionsStoreError
from tools.wrapper.vikunja_client import VikunjaClient, VikunjaError, VikunjaHTTPError

HOST = "127.0.0.1"
BOARD_URL = "http://board.test/api/v1"
BOARD_WEB = "http://board.test"
PROJECT_ID = 7
VIEW_ID = 30
PROJECT_TITLE = "cascade-rig"

SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    ended_at REAL,
    archived INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT,
    timestamp REAL NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
);
"""

# (session id, title, ended_at, archived)
SessionSpec = tuple[str, str | None, float | None, int]
# (session id, role, content, timestamp, active)
MessageSpec = tuple[str, str, str, float, int]


class FakeTransport:
    """Answers canned payloads by (method, path) and records what the client asked for.

    The query string is dropped from the key on purpose: these tests speak about endpoints, not
    about the page size the client happens to use.
    """

    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, str | None]] = []

    def request(self, method: str, path: str, body: Any = None, token: str | None = None) -> tuple[int, Any]:
        self.calls.append((method, path.split("?")[0], token))
        return self.routes.get((method, path.split("?")[0]), (404, {"message": "Not Found"}))


def card_payload(
    cid: int,
    title: str,
    labels: tuple[tuple[str, str], ...] = (),
    blockers: tuple[tuple[int, bool], ...] = (),
) -> dict[str, Any]:
    """One task as the board returns it; `blockers` is (id, done) of every card that blocks this one."""
    return {
        "id": cid,
        "title": title,
        "project_id": PROJECT_ID,
        "labels": [{"title": name, "hex_color": colour} for name, colour in labels],
        "related_tasks": {
            "blocked": [{"id": other, "title": f"card {other}", "done": done} for other, done in blockers]
        },
    }


def board_routes(cards: Sequence[dict[str, Any]], *, kanban: bool = True) -> dict[tuple[str, str], tuple[int, Any]]:
    """A board with one project, its kanban view and the given cards in the `Plan` column."""
    views: list[dict[str, Any]] = [{"id": 27, "title": "List", "view_kind": "list"}]
    if kanban:
        views.append({"id": VIEW_ID, "title": "Kanban", "view_kind": "kanban"})
    return {
        ("GET", "/projects"): (200, [{"id": PROJECT_ID, "title": PROJECT_TITLE, "is_archived": False}]),
        ("GET", f"/projects/{PROJECT_ID}/views"): (200, views),
        ("GET", f"/projects/{PROJECT_ID}/views/{VIEW_ID}/tasks"): (
            200,
            [{"id": 48, "title": "Plan", "tasks": list(cards)}],
        ),
    }


def board_client(cards: Sequence[dict[str, Any]], *, kanban: bool = True) -> VikunjaClient:
    """A real client over a fake board that serves the given cards of the `Plan` column."""
    return VikunjaClient(BOARD_URL, token="test-token", transport=FakeTransport(board_routes(cards, kanban=kanban)))


def write_store(
    path: Path,
    sessions: Sequence[SessionSpec] = (),
    messages: Sequence[MessageSpec] = (),
) -> Path:
    """Write a session store with the columns the rail reads and return its path."""
    connection = sqlite3.connect(path)
    try:
        connection.executescript(SCHEMA)
        connection.executemany("INSERT INTO sessions (id, title, ended_at, archived) VALUES (?, ?, ?, ?)", sessions)
        connection.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?, ?, ?, ?, ?)",
            messages,
        )
        connection.commit()
    finally:
        connection.close()
    return path


def fetch(url: str, *, body: bytes | None = None, method: str = "GET") -> tuple[int, Any]:
    """Send one request to a started rail and return the status with the decoded JSON answer."""
    address = urllib.parse.urlsplit(url)
    connection = HTTPConnection(address.hostname, address.port, timeout=5)
    try:
        connection.request(method, address.path or "/", body=body)
        response = connection.getresponse()
        return response.status, json.loads(response.read())
    finally:
        connection.close()


def post_rail(url: str, payload: object) -> tuple[int, Any]:
    """Post a JSON payload to `/api/rail` of a started rail and return the status and the answer."""
    return fetch(url + "/api/rail", body=json.dumps(payload).encode("utf-8"), method="POST")


def cli_argv(
    static_dir: Path,
    store_path: Path,
    *,
    omit: str | None = None,
    port: str = "0",
) -> list[str]:
    """The command line of the rail with every required option, minus the one to leave out."""
    options = {"--host": HOST, "--port": port, "--static-dir": str(static_dir), "--db": str(store_path)}
    argv: list[str] = []
    for option, value in options.items():
        if option == omit:
            continue
        argv.extend([option, value])
    return argv


class InjectedServer:
    """A rail server that records being started and stopped instead of binding a socket."""

    def __init__(self, url: str = "http://127.0.0.1:54321") -> None:
        self._url = url
        self.started = 0
        self.stopped = 0

    @property
    def url(self) -> str:
        return self._url

    def start(self) -> None:
        self.started += 1

    def stop(self) -> None:
        self.stopped += 1


def _return_immediately() -> None:
    """Stand in for the wait-for-interrupt of the CLI, which would block the test forever."""
    pass


@pytest.fixture
def static_dir(tmp_path: Path) -> Path:
    """A directory shaped like the rail's page."""
    root = tmp_path / "static"
    root.mkdir()
    (root / "index.html").write_text("<!doctype html><title>Hermes rail</title>", encoding="utf-8")
    return root


@pytest.fixture
def store_path(tmp_path: Path) -> Path:
    """An existing, empty session store: the rail must open it, not only remember the path."""
    return write_store(tmp_path / "state.db")


@pytest.fixture
def board_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the board client at an address and a token through the environment it reads."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("VIKUNJA_URL", BOARD_URL)
    monkeypatch.setenv("VIKUNJA_TOKEN", "test-token")


@pytest.fixture
def injected(monkeypatch: pytest.MonkeyPatch) -> tuple[InjectedServer, dict[str, Any]]:
    """Replace the server builder and the blocking wait of the CLI; report what was built."""
    server = InjectedServer()
    built: dict[str, Any] = {}

    def build(**kwargs: Any) -> InjectedServer:
        built.update(kwargs)
        return server

    monkeypatch.setattr(rail_app, "build_server", build)
    monkeypatch.setattr(rail_app, "_wait_for_interrupt", _return_immediately)
    return server, built


# --------------------------------------------------------------------------- cards source


def test_cards_source_maps_a_card_to_a_rail_view_exactly() -> None:
    board = board_client([card_payload(184, "Rail", [("ui", "#5b8ff9"), ("blocked", "#ff0000")])])

    views = rail_app.cards_source(board)("cascade-rig")

    assert list(views) == [
        CardView(
            id=184,
            title="Rail",
            labels=(("ui", "#5b8ff9"), ("blocked", "#ff0000")),
            blocked=True,
            url=f"{BOARD_WEB}/projects/{PROJECT_ID}/tasks/184",
        )
    ]


def test_cards_source_keeps_the_board_order() -> None:
    board = board_client([card_payload(184, "First"), card_payload(180, "Second")])

    views = rail_app.cards_source(board)("cascade-rig")

    assert [view.id for view in views] == [184, 180]


@pytest.mark.parametrize(
    ("labels", "blockers", "blocked"),
    [
        ((), (), False),
        ((("ui", "#5b8ff9"),), (), False),
        ((("blocked", "#ff0000"),), (), True),
        ((), ((180, False),), True),
        ((), ((180, True),), False),
    ],
    ids=["bare card", "free label", "blocked label", "unfinished blocker", "finished blocker"],
)
def test_cards_source_takes_the_blocked_flag_from_the_card(
    labels: tuple[tuple[str, str], ...],
    blockers: tuple[tuple[int, bool], ...],
    blocked: bool,
) -> None:
    board = board_client([card_payload(184, "Rail", labels, blockers)])

    views = rail_app.cards_source(board)("cascade-rig")

    assert [view.blocked for view in views] == [blocked]


@pytest.mark.property
@settings(max_examples=25, deadline=None)
@given(
    cards=st.lists(
        st.tuples(
            st.text(max_size=20),
            st.lists(st.tuples(st.text(max_size=10), st.text(max_size=7)), max_size=3),
        ),
        max_size=5,
    )
)
def test_every_card_of_the_board_becomes_one_view_that_keeps_its_labels_and_url(
    cards: list[tuple[str, list[tuple[str, str]]]],
) -> None:
    payloads = [
        card_payload(184 + index, title, tuple((name, colour) for name, colour in labels))
        for index, (title, labels) in enumerate(cards)
    ]

    views = list(rail_app.cards_source(board_client(payloads))("cascade-rig"))

    assert [view.id for view in views] == [184 + index for index in range(len(cards))]
    assert [view.title for view in views] == [title for title, _ in cards]
    assert [view.labels for view in views] == [tuple((name, colour) for name, colour in labels) for _, labels in cards]
    assert [view.url for view in views] == [
        f"{BOARD_WEB}/projects/{PROJECT_ID}/tasks/{184 + index}" for index in range(len(cards))
    ]


def test_cards_source_translates_an_unknown_project_into_the_server_error() -> None:
    board = VikunjaClient(
        BOARD_URL,
        token="test-token",
        transport=FakeTransport({("GET", "/projects"): (200, [])}),
    )

    with pytest.raises(UnknownProjectError) as raised:
        rail_app.cards_source(board)("ghost")

    assert "ghost" in str(raised.value)


def test_cards_source_does_not_swallow_a_board_error_status() -> None:
    board = VikunjaClient(
        BOARD_URL,
        token="test-token",
        transport=FakeTransport({("GET", "/projects"): (500, {"message": "boom"})}),
    )

    with pytest.raises(VikunjaHTTPError) as raised:
        rail_app.cards_source(board)("cascade-rig")

    assert raised.value.status == 500


def test_cards_source_does_not_swallow_a_board_without_a_kanban_view() -> None:
    board = board_client([card_payload(184, "Rail")], kanban=False)

    with pytest.raises(VikunjaError) as raised:
        rail_app.cards_source(board)("cascade-rig")

    assert not isinstance(raised.value, UnknownProjectError)


# --------------------------------------------------------------------------- sessions source


def test_sessions_source_passes_the_store_rows_through_unchanged(tmp_path: Path) -> None:
    store = write_store(
        tmp_path / "state.db",
        [("s-old", "old", None, 0), ("s-new", "new", None, 0)],
        [("s-old", "user", "hi", 100.0, 1), ("s-new", "user", "hi", 300.0, 1)],
    )

    rows = rail_app.sessions_source(SessionsStore(store))(50)

    assert list(rows) == [
        SessionRow(id="s-new", title="new", last_message_at=300.0, running=True, message_count=1),
        SessionRow(id="s-old", title="old", last_message_at=100.0, running=True, message_count=1),
    ]


def test_sessions_source_asks_the_store_for_at_most_the_limit(tmp_path: Path) -> None:
    store = write_store(
        tmp_path / "state.db",
        [("s-old", "old", None, 0), ("s-new", "new", None, 0)],
        [("s-old", "user", "hi", 100.0, 1), ("s-new", "user", "hi", 300.0, 1)],
    )

    rows = rail_app.sessions_source(SessionsStore(store))(1)

    assert [row.id for row in rows] == ["s-new"]


# --------------------------------------------------------------------------- the builder


def test_build_server_returns_a_server_that_is_not_started_yet(static_dir: Path, store_path: Path) -> None:
    server = rail_app.build_server(
        host=HOST,
        port=0,
        static_dir=static_dir,
        db_path=store_path,
        client=board_client([card_payload(184, "Rail")]),
    )

    assert isinstance(server, RailServer)
    assert server.url is None


def test_build_server_raises_file_not_found_for_a_missing_static_directory(tmp_path: Path, store_path: Path) -> None:
    missing = tmp_path / "absent"

    with pytest.raises(FileNotFoundError) as raised:
        rail_app.build_server(
            host=HOST,
            port=0,
            static_dir=missing,
            db_path=store_path,
            client=board_client([card_payload(184, "Rail")]),
        )

    assert str(missing) in str(raised.value)


def test_build_server_raises_a_store_error_for_a_missing_session_store(tmp_path: Path, static_dir: Path) -> None:
    missing = tmp_path / "absent.db"

    with pytest.raises(SessionsStoreError) as raised:
        rail_app.build_server(
            host=HOST,
            port=0,
            static_dir=static_dir,
            db_path=missing,
            client=board_client([card_payload(184, "Rail")]),
        )

    assert str(missing) in str(raised.value)


def test_the_built_server_answers_the_cards_mapped_from_the_board_client(static_dir: Path, store_path: Path) -> None:
    server = rail_app.build_server(
        host=HOST,
        port=0,
        static_dir=static_dir,
        db_path=store_path,
        client=board_client([card_payload(184, "Rail", [("ui", "#5b8ff9"), ("blocked", "#ff0000")])]),
    )
    server.start()
    try:
        status, body = post_rail(server.url, {"project": PROJECT_TITLE, "seen": {}, "lang": "en"})
    finally:
        server.stop()

    assert status == 200
    assert body["cards"] == [
        {
            "id": 184,
            "title": "Rail",
            "labels": [["ui", "#5b8ff9"], ["blocked", "#ff0000"]],
            "blocked": True,
            "url": f"{BOARD_WEB}/projects/{PROJECT_ID}/tasks/184",
        }
    ]
    assert body["sessions"] == []


# --------------------------------------------------------------------------- the command line


@pytest.mark.parametrize("missing", ["--host", "--port", "--static-dir", "--db"])
def test_the_cli_rejects_a_missing_required_option(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], missing: str
) -> None:
    argv = cli_argv(tmp_path / "static", tmp_path / "state.db", omit=missing)

    with pytest.raises(SystemExit) as raised:
        rail_app.main(argv)

    assert raised.value.code == 2
    assert missing in capsys.readouterr().err


def test_the_cli_rejects_a_port_that_is_not_an_integer(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    argv = cli_argv(tmp_path / "static", tmp_path / "state.db", port="http")

    with pytest.raises(SystemExit) as raised:
        rail_app.main(argv)

    assert raised.value.code == 2
    assert "--port" in capsys.readouterr().err


def test_the_cli_accepts_port_zero_and_prints_the_url_it_bound(
    board_configured: None,
    static_dir: Path,
    store_path: Path,
    injected: tuple[InjectedServer, dict[str, Any]],
    capsys: pytest.CaptureFixture[str],
) -> None:
    server, _ = injected

    code = rail_app.main(cli_argv(static_dir, store_path))

    captured = capsys.readouterr()
    assert code == 0
    assert len(captured.out.splitlines()) == 1
    assert server.url in captured.out


def test_the_cli_hands_the_address_the_paths_and_the_client_to_the_builder(
    board_configured: None,
    static_dir: Path,
    store_path: Path,
    injected: tuple[InjectedServer, dict[str, Any]],
) -> None:
    _, built = injected

    rail_app.main(cli_argv(static_dir, store_path))

    assert built["host"] == HOST
    assert built["port"] == 0
    assert built["static_dir"] == static_dir
    assert built["db_path"] == store_path
    assert isinstance(built["client"], VikunjaClient)
    assert built["client"].base_url == BOARD_URL


def test_the_cli_starts_and_stops_the_server_it_built(
    board_configured: None,
    static_dir: Path,
    store_path: Path,
    injected: tuple[InjectedServer, dict[str, Any]],
) -> None:
    server, _ = injected

    rail_app.main(cli_argv(static_dir, store_path))

    assert (server.started, server.stopped) == (1, 1)


def test_the_cli_reports_a_missing_static_directory_and_returns_one(
    board_configured: None,
    tmp_path: Path,
    store_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "absent"

    code = rail_app.main(cli_argv(missing, store_path))

    captured = capsys.readouterr()
    assert code == 1
    assert "ERROR" in captured.err
    assert str(missing) in captured.err
    assert captured.out == ""


def test_the_cli_reports_a_missing_session_store_and_returns_one(
    board_configured: None,
    tmp_path: Path,
    static_dir: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    missing = tmp_path / "absent.db"

    code = rail_app.main(cli_argv(static_dir, missing))

    captured = capsys.readouterr()
    assert code == 1
    assert "ERROR" in captured.err
    assert str(missing) in captured.err
    assert captured.out == ""


def test_the_cli_reports_an_unconfigured_board_client_and_returns_one(
    tmp_path: Path,
    static_dir: Path,
    store_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    monkeypatch.delenv("VIKUNJA_TOKEN", raising=False)

    code = rail_app.main(cli_argv(static_dir, store_path))

    captured = capsys.readouterr()
    assert code == 1
    assert "ERROR" in captured.err
    assert "VIKUNJA_URL" in captured.err
    assert captured.out == ""
