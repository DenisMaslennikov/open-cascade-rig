"""Behaviour contract for the rail's read-only view of the Hermes session store.

The store is the SQLite file the dashboard writes. These tests build their own files with the columns
the rail reads, so nothing here touches the owner's live state and nothing here writes to a store.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from tools.wrapper.sessions_store import SessionRow, SessionsStore, SessionsStoreError

SCHEMA = """
DROP TABLE IF EXISTS messages;
DROP TABLE IF EXISTS sessions;
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    title TEXT,
    display_name TEXT,
    started_at REAL NOT NULL DEFAULT 0,
    ended_at REAL,
    archived INTEGER NOT NULL DEFAULT 0,
    hidden INTEGER NOT NULL DEFAULT 0,
    last_activity_at REAL,
    message_count INTEGER NOT NULL DEFAULT 0
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

# (session id, title, ended_at, archived); `message_count` is deliberately left at its zero default,
# so a row that reports the real number proves it counts the messages instead of trusting the column.
SessionSpec = tuple[str, str | None, float | None, int]
# (session id, role, content, timestamp, active)
MessageSpec = tuple[str, str, str, float, int]


def make_store(
    path: Path,
    sessions: Sequence[SessionSpec] = (),
    messages: Sequence[MessageSpec] = (),
) -> Path:
    """Write a store the rail can read and return its path."""
    con = sqlite3.connect(path)
    try:
        con.executescript(SCHEMA)
        con.executemany(
            "INSERT INTO sessions (id, title, ended_at, archived) VALUES (?, ?, ?, ?)",
            sessions,
        )
        con.executemany(
            "INSERT INTO messages (session_id, role, content, timestamp, active) VALUES (?, ?, ?, ?, ?)",
            messages,
        )
        con.commit()
    finally:
        con.close()
    return path


def test_lists_the_session_with_the_newest_message_first(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s-old", "old", None, 0), ("s-new", "new", None, 0)],
        [("s-old", "user", "hi", 100.0, 1), ("s-new", "user", "hi", 300.0, 1)],
    )

    rows = SessionsStore(store).active()

    assert rows == [
        SessionRow(id="s-new", title="new", last_message_at=300.0, running=True, message_count=1),
        SessionRow(id="s-old", title="old", last_message_at=100.0, running=True, message_count=1),
    ]


def test_lists_sessions_without_messages_after_the_timed_ones(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s-silent", "silent", None, 0), ("s-mid", "mid", None, 0), ("s-late", "late", None, 0)],
        [("s-mid", "user", "hi", 100.0, 1), ("s-late", "user", "hi", 200.0, 1)],
    )

    rows = SessionsStore(store).active()

    assert [row.id for row in rows] == ["s-late", "s-mid", "s-silent"]


def test_archived_sessions_are_not_listed(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s-live", "live", None, 0), ("s-archived", "archived", None, 1)],
        [("s-live", "user", "hi", 100.0, 1), ("s-archived", "user", "hi", 200.0, 1)],
    )

    rows = SessionsStore(store).active()

    assert [row.id for row in rows] == ["s-live"]


def test_a_session_runs_until_it_ends(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s-open", "open", None, 0), ("s-ended", "ended", 1_700_000_000.0, 0)],
        [("s-open", "user", "hi", 100.0, 1), ("s-ended", "user", "hi", 200.0, 1)],
    )

    running = {row.id: row.running for row in SessionsStore(store).active()}

    assert running == {"s-open": True, "s-ended": False}


def test_the_title_is_the_session_title(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s1", "Deploy the rail", None, 0)],
        [("s1", "user", "hello", 100.0, 1)],
    )

    rows = SessionsStore(store).active()

    assert rows[0].title == "Deploy the rail"


@pytest.mark.parametrize("title", [None, ""])
def test_an_empty_title_falls_back_to_the_first_user_message_cut_to_80_characters(
    tmp_path: Path, title: str | None
) -> None:
    long_message = "y" * 100
    store = make_store(
        tmp_path / "state.db",
        [("s1", title, None, 0)],
        [
            ("s1", "assistant", "a first turn", 1.0, 1),
            ("s1", "user", long_message, 2.0, 1),
            ("s1", "user", "a later turn", 3.0, 1),
        ],
    )

    rows = SessionsStore(store).active()

    assert rows[0].title == "y" * 80


@pytest.mark.parametrize(
    "messages",
    [[], [("s1", "assistant", "a turn", 1.0, 1)]],
    ids=["no messages", "no user message"],
)
def test_a_session_without_a_user_message_is_untitled(tmp_path: Path, messages: list[MessageSpec]) -> None:
    store = make_store(tmp_path / "state.db", [("s1", "", None, 0)], messages)

    rows = SessionsStore(store).active()

    assert rows[0].title == "untitled"


def test_a_session_without_messages_has_no_timestamp_and_no_count(tmp_path: Path) -> None:
    store = make_store(tmp_path / "state.db", [("s1", "fresh", None, 0)])

    rows = SessionsStore(store).active()

    assert rows == [SessionRow(id="s1", title="fresh", last_message_at=None, running=True, message_count=0)]


def test_inactive_messages_are_neither_timed_nor_counted(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s1", "kept", None, 0)],
        [("s1", "user", "kept", 10.0, 1), ("s1", "user", "dropped", 20.0, 0)],
    )

    rows = SessionsStore(store).active()

    assert (rows[0].last_message_at, rows[0].message_count) == (10.0, 1)


def test_returns_at_most_the_limit_the_caller_asked_for(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s1", "one", None, 0), ("s2", "two", None, 0), ("s3", "three", None, 0)],
        [("s1", "user", "hi", 100.0, 1), ("s2", "user", "hi", 200.0, 1), ("s3", "user", "hi", 300.0, 1)],
    )

    rows = SessionsStore(store).active(limit=2)

    assert [row.id for row in rows] == ["s3", "s2"]


@pytest.mark.parametrize("limit", [0, -1])
def test_a_non_positive_limit_is_rejected(tmp_path: Path, limit: int) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s1", "one", None, 0)],
        [("s1", "user", "hi", 100.0, 1)],
    )

    with pytest.raises(ValueError):
        SessionsStore(store).active(limit=limit)


def test_a_missing_store_raises_an_error_that_names_the_path(tmp_path: Path) -> None:
    missing = tmp_path / "absent.db"

    with pytest.raises(SessionsStoreError) as caught:
        SessionsStore(missing).active()

    assert str(missing) in str(caught.value)
    assert not missing.exists()


def test_a_file_that_is_not_a_database_raises_an_error_that_names_the_path(tmp_path: Path) -> None:
    broken = tmp_path / "state.db"
    broken.write_text("this is not a SQLite database", encoding="utf-8")

    with pytest.raises(SessionsStoreError) as caught:
        SessionsStore(broken).active()

    assert str(broken) in str(caught.value)


def test_an_empty_store_lists_nothing(tmp_path: Path) -> None:
    store = make_store(tmp_path / "state.db")

    assert SessionsStore(store).active() == []


def test_the_store_is_opened_read_only(tmp_path: Path) -> None:
    store = make_store(
        tmp_path / "state.db",
        [("s1", "one", None, 0)],
        [("s1", "user", "hi", 100.0, 1)],
    )
    before = store.read_bytes()
    store.chmod(0o444)

    rows = SessionsStore(store).active()

    assert [row.id for row in rows] == ["s1"]
    assert store.read_bytes() == before
    assert [entry.name for entry in tmp_path.iterdir()] == ["state.db"]


@pytest.mark.property
@settings(max_examples=20, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(
    sessions=st.lists(
        st.tuples(st.uuids().map(str), st.integers(min_value=1, max_value=10_000)),
        max_size=4,
        unique_by=lambda pair: pair[1],
    ),
    limit=st.integers(min_value=1, max_value=4),
)
def test_the_limit_cuts_the_ordered_listing(tmp_path: Path, sessions: list[tuple[str, int]], limit: int) -> None:
    store = make_store(
        tmp_path / "state.db",
        [(session_id, session_id[:8], None, 0) for session_id, _ in sessions],
        [(session_id, "user", "hi", float(stamp), 1) for session_id, stamp in sessions],
    )

    full = SessionsStore(store).active(limit=max(len(sessions), 1))

    assert SessionsStore(store).active(limit=limit) == full[:limit]
