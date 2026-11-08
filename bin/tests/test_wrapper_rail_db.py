"""Behaviour of the wrapper rail's SQLite persistence."""

import sqlite3
from pathlib import Path

import pytest
from rail_db import Base, SeenStore, apply_pending_migrations, session_factory
from sqlalchemy import create_engine, text


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "rail-test.db"


def _prepare_schema(db_path: Path) -> None:
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    engine.dispose()


def _store(db_path: Path) -> SeenStore:
    _prepare_schema(db_path)
    return SeenStore(session_factory(db_path))


def test_session_factory_connects_to_the_given_database_file(db_path: Path) -> None:
    factory = session_factory(db_path)

    with factory() as session:
        assert session.execute(text("select 1")).scalar_one() == 1

    assert db_path.exists()


def test_store_and_load_seen_round_trips_marks(db_path: Path) -> None:
    store = _store(db_path)
    marks = {"session:alpha": 100.0, "card:beta": 200.5}

    store.store_seen("project-a", marks)

    assert store.load_seen("project-a") == marks


def test_load_seen_returns_empty_for_an_unknown_project(db_path: Path) -> None:
    store = _store(db_path)

    assert store.load_seen("project-none") == {}


def test_stored_marks_are_isolated_by_project(db_path: Path) -> None:
    store = _store(db_path)

    store.store_seen("project-a", {"a": 1.0})
    store.store_seen("project-b", {"b": 2.0})

    assert store.load_seen("project-a") == {"a": 1.0}
    assert store.load_seen("project-b") == {"b": 2.0}


def test_store_seen_replaces_the_projects_previous_marks(db_path: Path) -> None:
    store = _store(db_path)

    store.store_seen("project-a", {"old": 1.0, "kept": 2.0})
    store.store_seen("project-a", {"kept": 3.0, "new": 4.0})

    assert store.load_seen("project-a") == {"kept": 3.0, "new": 4.0}


def test_store_seen_with_an_empty_map_clears_the_project(db_path: Path) -> None:
    store = _store(db_path)

    store.store_seen("project-a", {"old": 1.0})
    store.store_seen("project-a", {})

    assert store.load_seen("project-a") == {}


def test_apply_pending_migrations_creates_the_seen_schema_at_revision_0001(db_path: Path) -> None:
    apply_pending_migrations(db_path)

    with sqlite3.connect(db_path) as connection:
        tables = {row[0] for row in connection.execute("select name from sqlite_master where type = 'table'")}
        version = connection.execute("select version_num from alembic_version").fetchone()

    assert "seen" in tables
    assert "alembic_version" in tables
    assert version == ("0001",)


def test_apply_pending_migrations_twice_is_a_noop(db_path: Path) -> None:
    apply_pending_migrations(db_path)
    apply_pending_migrations(db_path)

    with sqlite3.connect(db_path) as connection:
        versions = connection.execute("select version_num from alembic_version").fetchall()

    assert versions == [("0001",)]
