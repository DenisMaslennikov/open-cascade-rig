"""Frozen contract for the wrapper rail's own SQLite persistence.

The tests cover the session factory, the seen-map store, the pending-migration entry point and the
parity between the alembic migrations and the SQLAlchemy model. Every test works on temporary SQLite
files only: no board, no gateway, no network.
"""

from __future__ import annotations

import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy import text

from tools.wrapper.rail_db import Base, SeenStore, apply_pending_migrations, session_factory

ALEMBIC_VERSION = 'alembic_version'

SEEN_TEXT = st.text(
    alphabet=st.characters(blacklist_categories=('Cs',), blacklist_characters='\x00'),
    max_size=24,
)



def model_tables() -> set[str]:
    """Return the table names the SQLAlchemy model declares.

    Returns:
        The keys of the model metadata, one per declared table.
    """
    return set(Base.metadata.tables)



def stored_tables(db_path: str) -> set[str]:
    """Return the table names a SQLite file holds.

    Arguments:
        db_path: filesystem path of the SQLite file.

    Returns:
        The names of the tables in the file, the alembic bookkeeping table included.
    """
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()
    return {row[0] for row in rows}



def stored_columns(db_path: str, table: str) -> set[str]:
    """Return the column names of one table in a SQLite file.

    Arguments:
        db_path: filesystem path of the SQLite file.
        table: name of the table to inspect.

    Returns:
        The column names PRAGMA reports; empty when the table does not exist.
    """
    with closing(sqlite3.connect(db_path)) as connection:
        rows = connection.execute(f'PRAGMA table_info("{table}")').fetchall()
    return {row[1] for row in rows}



def seen_store(db_path: str, project: str) -> SeenStore:
    """Open the seen store of one project on one database file.

    Arguments:
        db_path: filesystem path of the SQLite file.
        project: project slug the store isolates its keys by.

    Returns:
        A store bound to the given project and file.
    """
    return SeenStore(session_factory(db_path), project)



@pytest.fixture
def rail_db(tmp_path: Path) -> str:
    """Return a path to a SQLite file that does not exist yet."""
    return str(tmp_path / 'rail.sqlite3')



@pytest.fixture
def migrated_db(rail_db: str) -> str:
    """Return a SQLite file with the pending migrations applied."""
    apply_pending_migrations(rail_db)
    return rail_db



def test_migrations_create_the_model_schema_on_a_fresh_file(rail_db: str) -> None:
    apply_pending_migrations(rail_db)

    assert stored_tables(rail_db) - {ALEMBIC_VERSION} == model_tables()



def test_migrations_run_through_alembic(rail_db: str) -> None:
    apply_pending_migrations(rail_db)

    assert ALEMBIC_VERSION in stored_tables(rail_db)



def test_migrated_columns_match_the_model_columns(migrated_db: str) -> None:
    for name, table in Base.metadata.tables.items():
        assert stored_columns(migrated_db, name) == set(table.columns.keys()), name



def test_migrations_run_again_on_an_already_migrated_file(migrated_db: str) -> None:
    seen_store(migrated_db, 'accc').store_seen({'task:1': 'hash-a'})

    apply_pending_migrations(migrated_db)

    assert seen_store(migrated_db, 'accc').load_seen() == {'task:1': 'hash-a'}



def test_the_session_factory_opens_sessions_on_the_migrated_file(migrated_db: str) -> None:
    factory = session_factory(migrated_db)

    with factory() as session:
        names = set(session.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'")).scalars())

    assert ALEMBIC_VERSION in names



def test_a_stored_seen_map_loads_back_unchanged(migrated_db: str) -> None:
    store = seen_store(migrated_db, 'accc')

    store.store_seen({'task:1': 'hash-a', 'task:2': 'hash-b'})

    assert store.load_seen() == {'task:1': 'hash-a', 'task:2': 'hash-b'}



def test_a_project_that_never_stored_loads_an_empty_map(migrated_db: str) -> None:
    assert seen_store(migrated_db, 'accc').load_seen() == {}



def test_a_second_store_on_the_same_file_sees_the_first_write(migrated_db: str) -> None:
    seen_store(migrated_db, 'accc').store_seen({'task:1': 'hash-a'})

    assert seen_store(migrated_db, 'accc').load_seen() == {'task:1': 'hash-a'}



def test_storing_the_same_map_twice_leaves_it_unchanged(migrated_db: str) -> None:
    store = seen_store(migrated_db, 'accc')
    seen = {'task:1': 'hash-a', 'task:2': 'hash-b'}

    store.store_seen(seen)
    store.store_seen(seen)

    assert store.load_seen() == seen



def test_two_projects_on_one_file_do_not_see_each_other(migrated_db: str) -> None:
    alpha = seen_store(migrated_db, 'alpha')
    beta = seen_store(migrated_db, 'beta')

    alpha.store_seen({'task:1': 'alpha-hash'})
    beta.store_seen({'task:9': 'beta-hash'})

    assert alpha.load_seen() == {'task:1': 'alpha-hash'}
    assert beta.load_seen() == {'task:9': 'beta-hash'}



def test_the_same_key_in_two_projects_keeps_its_own_value(migrated_db: str) -> None:
    alpha = seen_store(migrated_db, 'alpha')
    beta = seen_store(migrated_db, 'beta')

    alpha.store_seen({'task:1': 'alpha-hash'})
    beta.store_seen({'task:1': 'beta-hash'})

    assert alpha.load_seen() == {'task:1': 'alpha-hash'}
    assert beta.load_seen() == {'task:1': 'beta-hash'}



@settings(max_examples=20, deadline=None)
@given(seen=st.dictionaries(SEEN_TEXT, SEEN_TEXT, max_size=8))
def test_any_seen_map_survives_the_round_trip(seen: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = str(Path(directory) / 'rail.sqlite3')
        apply_pending_migrations(db_path)
        store = seen_store(db_path, 'accc')

        store.store_seen(seen)

        assert store.load_seen() == seen



@settings(max_examples=15, deadline=None)
@given(
    alpha=st.dictionaries(SEEN_TEXT, SEEN_TEXT, max_size=5),
    beta=st.dictionaries(SEEN_TEXT, SEEN_TEXT, max_size=5),
)
def test_projects_on_one_file_keep_their_own_maps(alpha: dict[str, str], beta: dict[str, str]) -> None:
    with tempfile.TemporaryDirectory() as directory:
        db_path = str(Path(directory) / 'rail.sqlite3')
        apply_pending_migrations(db_path)

        seen_store(db_path, 'alpha').store_seen(alpha)
        seen_store(db_path, 'beta').store_seen(beta)

        assert seen_store(db_path, 'alpha').load_seen() == alpha
        assert seen_store(db_path, 'beta').load_seen() == beta
