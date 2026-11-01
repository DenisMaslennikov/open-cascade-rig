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

from dataclasses import dataclass
from pathlib import Path


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
        raise NotImplementedError

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
        raise NotImplementedError
