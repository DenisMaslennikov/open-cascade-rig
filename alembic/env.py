"""Alembic environment for the wrapper's own SQLite database.

The database path is never guessed: it arrives as the ``db_path`` -x option or as the
``WRAPPER_DB_PATH`` variable, and the command stops with an instruction when neither is set.
The migration context is configured inside the run functions, so this module can be imported
without a context being set up.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

DB_PATH_OPTION = "db_path"
DB_PATH_VARIABLE = "WRAPPER_DB_PATH"

__all__ = [
    "DB_PATH_OPTION",
    "DB_PATH_VARIABLE",
    "resolve_database_path",
    "run_migrations_offline",
    "run_migrations_online",
]


def resolve_database_path(x_args: Mapping[str, str], environ: Mapping[str, str]) -> Path:
    """Return the SQLite path the migration context must bind to.

    Arguments:
        x_args: the values passed as ``alembic -x name=value``, keyed by name.
        environ: the process environment, keyed by variable name.

    Returns:
        The path named by the ``db_path`` option, or by ``WRAPPER_DB_PATH`` when the option is absent.

    Raises:
        SystemExit: when neither the option nor the variable is set; the message names what to pass.
    """
    raise NotImplementedError


def run_migrations_offline() -> None:
    """Emit the pending migrations as SQL against the resolved database path.

    Raises:
        SystemExit: when no database path is configured.
    """
    raise NotImplementedError


def run_migrations_online() -> None:
    """Apply the pending migrations to the resolved SQLite database over a live connection.

    Raises:
        SystemExit: when no database path is configured.
    """
    raise NotImplementedError
