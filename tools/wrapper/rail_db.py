"""SQLite persistence for the wrapper rail: the seen marks and their schema.

The module owns the wrapper's own SQLAlchemy metadata, the Seen model, the
session factory, the SeenStore seam injected into the rail server, and the
Alembic upgrade entry point. The schema is created by the Alembic tree, never by
create_all.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config as AlembicConfig
from sqlalchemy import Float, MetaData, String, create_engine, delete, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from alembic import command

__all__ = [
    "RAIL_METADATA",
    "Base",
    "Seen",
    "SeenMap",
    "SeenStore",
    "apply_pending_migrations",
    "session_factory",
]

RAIL_METADATA = MetaData()

SeenMap = dict[str, float]


class Base(DeclarativeBase):
    """Declarative base bound to RAIL_METADATA."""

    metadata = RAIL_METADATA


class Seen(Base):
    """ORM model of the seen table.

    One row per seen mark, keyed by the project and the mark's identity as the
    request body shapes it.
    """

    __tablename__ = "seen"

    project: Mapped[str] = mapped_column(String, primary_key=True)
    mark: Mapped[str] = mapped_column(String, primary_key=True)
    seen_at: Mapped[float] = mapped_column(Float, nullable=False)


def session_factory(db_path: Path) -> sessionmaker[Session]:
    """Create a session factory for the SQLite database at db_path.

    Arguments:
        db_path: Filesystem path of the SQLite database.

    Returns:
        A sessionmaker bound to the database.
    """
    engine = create_engine(f"sqlite:///{db_path}")
    return sessionmaker(bind=engine)


class SeenStore:
    """Persistence seam for seen marks, injected into the rail server.

    Arguments:
        make_session: A session factory matching session_factory.
    """

    def __init__(self, make_session: sessionmaker[Session]) -> None:
        """Store the session factory.

        Arguments:
            make_session: A session factory matching session_factory.
        """
        self._make_session = make_session

    def load_seen(self, project: str) -> SeenMap:
        """Load the seen marks stored for a project.

        Arguments:
            project: Project identifier.

        Returns:
            A mapping from mark identity to epoch seconds; empty when the
            project has no marks.
        """
        with self._make_session() as session:
            rows = session.scalars(select(Seen).where(Seen.project == project))
            return {row.mark: row.seen_at for row in rows}

    def store_seen(self, project: str, seen: SeenMap) -> None:
        """Store the seen marks for a project.

        The project's previous marks are replaced: marks absent from `seen`
        are deleted, the rest are upserted with the new epoch seconds.

        Arguments:
            project: Project identifier.
            seen: Mapping from mark identity to epoch seconds.

        Returns:
            None.
        """
        with self._make_session() as session:
            session.execute(delete(Seen).where(Seen.project == project))
            session.bulk_insert_mappings(
                Seen,
                [{"project": project, "mark": mark, "seen_at": seen_at} for mark, seen_at in seen.items()],
            )
            session.commit()


def apply_pending_migrations(db_path: Path) -> None:
    """Upgrade the SQLite database at db_path to the Alembic head revision.

    The Alembic script location is resolved from this module's position, so the
    function works from any current working directory. The database path travels in
    the config's attributes — the programmatic counterpart of the ``-x db_path``
    option that ``alembic/env.py`` reads. Calling it on a database already at head
    is a no-op.

    Arguments:
        db_path: Filesystem path of the SQLite database.

    Returns:
        None.
    """
    repository_root = Path(__file__).resolve().parents[2]
    if str(repository_root) not in sys.path:
        sys.path.insert(0, str(repository_root))
    alembic = AlembicConfig(str(repository_root / "alembic.ini"))
    alembic.set_main_option("script_location", str(repository_root / "alembic"))
    alembic.attributes["db_path"] = db_path
    command.upgrade(alembic, "head")
