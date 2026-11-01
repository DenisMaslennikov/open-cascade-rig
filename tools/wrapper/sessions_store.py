"""Read-only view of the Hermes session store for the wrapper rail.

The dashboard keeps every session in one SQLite file: the `sessions` table holds the session itself
and the `messages` table holds its transcript. The rail only reads that file, and it opens it
read-only, so a rail that is open while Hermes writes can never take a lock on a transcript or
corrupt one.

Shape of the rows this module reads (the columns the rail needs, from the dashboard's schema):

- `sessions`: `id`, `title`, `ended_at`, `archived`.
- `messages`: `session_id`, `role`, `content`, `timestamp`, `active`.

`tools/` and `tools/wrapper/` are namespace packages: modules refer to each other by their absolute
path (`tools.wrapper.<module>`) with the repository root on `sys.path`.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

_UNTITLED = "untitled"
_TITLE_LIMIT = 80


class SessionsStoreError(Exception):
    """The session store could not be read: absent, unreadable, or not a SQLite database."""


@dataclass(frozen=True, slots=True)
class SessionRow:
    """One session, as the rail list needs it.

    Attributes:
        id: The session identifier.
        title: The display name; never empty (`untitled` when the session has no name at all).
        last_message_at: Epoch seconds of the newest active message, or None when the session has
            no active message.
        running: True while the session has not ended.
        message_count: Number of active messages in the session.
    """

    id: str
    title: str
    last_message_at: float | None
    running: bool
    message_count: int


class SessionsStore:
    """Reads the sessions of a Hermes state database without writing to it.

    Arguments:
        db_path: Path of the SQLite file the dashboard writes.
    """

    def __init__(self, db_path: Path) -> None:
        """Remember where the store is; the file is not opened here.

        Arguments:
            db_path: Path of the SQLite file the dashboard writes.
        """
        self._db_path = Path(db_path)

    def active(self, limit: int = 50) -> list[SessionRow]:
        """List the sessions the rail must show, newest activity first.

        A session is listed when it is not archived (`sessions.archived` is zero). Its title is
        `sessions.title`; when that is NULL or empty the first user message is used instead (the
        earliest active message whose `role` is `user`), cut to 80 characters; when there is no such
        message either the title is the literal `untitled`. `running` is true when
        `sessions.ended_at` is NULL. `last_message_at` is the newest timestamp among the session's
        active messages and `message_count` is how many active messages it has; a message counts as
        active when `messages.active` is non-zero.

        The store is opened as a read-only SQLite URI (`file:<path>?mode=ro`): a call must succeed on
        a database file the caller cannot write and must leave the file byte-for-byte unchanged.

        Arguments:
            limit: Maximum number of rows to return; it must be positive.

        Returns:
            At most `limit` rows ordered by `last_message_at` descending, the sessions without any
            active message last. An existing store with no sessions yields an empty list.

        Raises:
            ValueError: `limit` is zero or negative.
            SessionsStoreError: The store is missing, cannot be opened, or is not a SQLite database.
                The message names the path.
        """
        if limit <= 0:
            raise ValueError(f"limit must be positive, got {limit}")
        if not self._db_path.is_file():
            raise SessionsStoreError(f"session store does not exist: {self._db_path}")

        # The three slashes after `file:` make SQLite treat the rest as an absolute path; the
        # filename is quoted so a character like `?` or `#` in a directory name stays part of it.
        uri = f"file:{quote(str(self._db_path.resolve()))}?mode=ro"
        try:
            connection = sqlite3.connect(uri, uri=True)
        except sqlite3.Error as error:
            raise SessionsStoreError(f"cannot open session store {self._db_path}: {error}") from error

        try:
            cursor = connection.execute(
                """
                SELECT s.id,
                       s.title,
                       s.ended_at,
                       MAX(m.timestamp),
                       COUNT(m.id),
                       (
                           SELECT m2.content
                           FROM messages m2
                           WHERE m2.session_id = s.id
                             AND m2.active != 0
                             AND m2.role = 'user'
                           ORDER BY m2.timestamp
                           LIMIT 1
                       )
                FROM sessions s
                LEFT JOIN messages m ON m.session_id = s.id AND m.active != 0
                GROUP BY s.id
                HAVING s.archived = 0
                ORDER BY MAX(m.timestamp) IS NULL, MAX(m.timestamp) DESC
                LIMIT ?
                """,
                (limit,),
            )
            rows = cursor.fetchall()
        except sqlite3.Error as error:
            raise SessionsStoreError(f"cannot read session store {self._db_path}: {error}") from error
        finally:
            connection.close()

        return [
            SessionRow(
                id=session_id,
                title=title if title else fallback_title[:_TITLE_LIMIT] if fallback_title else _UNTITLED,
                last_message_at=last_message_at,
                running=ended_at is None,
                message_count=active_message_count,
            )
            for session_id, title, ended_at, last_message_at, active_message_count, fallback_title in rows
        ]
