"""Alembic environment for the wrapper's own SQLite database.

The database path is never guessed: it arrives as the ``db_path`` -x option, as the
``WRAPPER_DB_PATH`` variable, or — for in-process runs through
``rail_db.apply_pending_migrations`` — as the ``db_path`` entry of the Alembic config's
attributes. The command stops with an instruction when none of them is set. The driver
stanza at the bottom runs the migrations; without it ``command.upgrade`` would load this
module and silently do nothing.
"""

from __future__ import annotations

import logging.config
import os
from collections.abc import Mapping
from pathlib import Path

from alembic import context
from sqlalchemy import create_engine

from tools.wrapper.rail_db import RAIL_METADATA

DB_PATH_OPTION = "db_path"
DB_PATH_VARIABLE = "WRAPPER_DB_PATH"

__all__ = [
    "DB_PATH_OPTION",
    "DB_PATH_VARIABLE",
    "resolve_database_path",
    "run_migrations_offline",
    "run_migrations_online",
]


config = context.config

if config.attributes.get("configure_logger", True):
    logging.config.fileConfig(config.config_file_name)  # noqa: S321

target_metadata = RAIL_METADATA


def resolve_database_path(
    x_args: Mapping[str, str],
    environ: Mapping[str, str],
    attributes: Mapping[str, object] | None = None,
) -> Path:
    """Return the SQLite path the migration context must bind to.

    Arguments:
        x_args: the values passed as ``alembic -x name=value``, keyed by name.
        environ: the process environment, keyed by variable name.
        attributes: the Alembic config's attributes dict; in-process callers deliver
            the path as its ``db_path`` entry, the programmatic counterpart of the
            ``-x`` option.

    Returns:
        The path named by the ``db_path`` option, by the config attributes, or by
        ``WRAPPER_DB_PATH`` when both are absent.

    Raises:
        SystemExit: when none of the three is set; the message names what to pass.
    """
    raw = x_args.get(DB_PATH_OPTION)
    if not raw or not raw.strip():
        raw = (attributes or {}).get(DB_PATH_OPTION)
    if isinstance(raw, Path):
        return raw.expanduser()
    if raw is None or not str(raw).strip():
        raise SystemExit(
            f"ERROR: no wrapper database configured: pass -x {DB_PATH_OPTION}=<path to alembic>"
            f" or export {DB_PATH_VARIABLE}=<path>"
        )
    return Path(str(raw)).expanduser()


def _sqlite_url(db_path: Path) -> str:
    """Return the SQLAlchemy URL of the SQLite file at db_path."""
    return f"sqlite:///{db_path}"


def run_migrations_offline() -> None:
    """Emit the pending migrations as SQL against the resolved database path.

    Raises:
        SystemExit: when no database path is configured.
    """
    url = _sqlite_url(resolve_database_path(_x_arguments(), os.environ, config.attributes))
    context.configure(
        url=url,
        target_metadata=RAIL_METADATA,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Apply the pending migrations to the resolved SQLite database over a live connection.

    Raises:
        SystemExit: when no database path is configured.
    """
    connectable = create_engine(
        _sqlite_url(resolve_database_path(_x_arguments(), os.environ, config.attributes))
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=RAIL_METADATA)

        with context.begin_transaction():
            context.run_migrations()

    connectable.dispose()


def _x_arguments() -> dict[str, str]:
    """Return the ``-x`` arguments as a dictionary, empty outside the CLI."""
    x_args = context.get_x_argument(as_dictionary=True)
    return dict(x_args) if isinstance(x_args, dict) else {}


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
