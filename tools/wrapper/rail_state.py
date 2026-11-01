"""Pure logic of the wrapper rail: ordering, unread marks, counters, relative times, the JSON body.

Every function here is a function of its arguments only: no clock, no database, no HTTP. The current
time and the language are passed in by `rail_server`, which is the only place that knows them.

`tools/` and `tools/wrapper/` are namespace packages: modules refer to each other by their absolute
path (`tools.wrapper.<module>`) with the repository root on `sys.path`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from typing import Any

from tools.wrapper.sessions_store import SessionRow


@dataclass(frozen=True, slots=True)
class SessionView:
    """One session row of the rail, ready to be serialised.

    Attributes:
        id: The session identifier.
        title: The display name.
        last_message_at: Epoch seconds of the newest active message, or None when there is none.
        running: True while the session has not ended.
        unread: True when the newest message is newer than the owner's last look.
        relative: The age of the newest message, already formatted for the rail.
    """

    id: str
    title: str
    last_message_at: float | None
    running: bool
    unread: bool
    relative: str


@dataclass(frozen=True, slots=True)
class CardView:
    """One board card of the rail, ready to be serialised.

    Attributes:
        id: The board task id.
        title: The card title.
        labels: The card labels as (title, hex colour) pairs.
        blocked: True when the card is blocked on the board.
        url: The card's page on the board.
    """

    id: int
    title: str
    labels: tuple[tuple[str, str], ...]
    blocked: bool
    url: str


def _recency(row: SessionRow) -> tuple[bool, float]:
    """Rank a session row for the rail list: dated rows first, newest moment first.

    Arguments:
        row: The session row to rank.

    Returns:
        `(True, 0.0)` for a session without a message, otherwise `(False, -last_message_at)`.
    """
    if row.last_message_at is None:
        return (True, 0.0)
    return (False, -row.last_message_at)


def sort_sessions(rows: Sequence[SessionRow]) -> list[SessionRow]:
    """Order sessions the way the rail list shows them.

    The newest `last_message_at` comes first, sessions without a last message come last, and equal
    positions are settled by session id descending. The argument is not modified.

    Arguments:
        rows: The sessions to order.

    Returns:
        A new list holding the same rows in rail order.
    """
    # The id order is settled first and the stable second pass keeps it for equal recency keys,
    # which is how ties end up ordered by id descending without a second sort key.
    by_id_descending = sorted(rows, key=lambda row: row.id, reverse=True)
    return sorted(by_id_descending, key=_recency)


def mark_unread(rows: Sequence[SessionRow], seen: Mapping[str, float]) -> list[SessionView]:
    """Turn session rows into rail views and mark the ones the owner has not looked at.

    A session is unread when its `last_message_at` is strictly newer than its stamp in `seen`; a
    session id missing from `seen` counts as a stamp of `0.0`, and a session without messages is
    never unread. The order of `rows` is kept as it is.

    `relative` is the empty string here: it needs the current time and the language, which this
    function does not receive. `rail_payload` fills it with `format_relative`.

    Arguments:
        rows: The sessions to convert, already in rail order.
        seen: The owner's last-look stamps, keyed by session id, in epoch seconds.

    Returns:
        One view per row, in the order of `rows`.
    """
    return [
        SessionView(
            id=row.id,
            title=row.title,
            last_message_at=row.last_message_at,
            running=row.running,
            unread=row.last_message_at is not None and row.last_message_at > seen.get(row.id, 0.0),
            relative="",
        )
        for row in rows
    ]


def rail_counters(cards: Sequence[CardView], sessions: Sequence[SessionView]) -> dict[str, int]:
    """Count what the rail's tab bar shows.

    Arguments:
        cards: The cards of the rail.
        sessions: The session views of the rail.

    Returns:
        `{"cards": len(cards), "sessions": <number of running sessions>, "unread": <number of unread
        sessions>}`.
    """
    return {
        "cards": len(cards),
        "sessions": sum(1 for view in sessions if view.running),
        "unread": sum(1 for view in sessions if view.unread),
    }


_UNIT_LABELS: dict[str, tuple[str, str, str, str]] = {
    "en": ("now", "min", "h", "d"),
    "ru": ("сейчас", "мин", "ч", "д"),
}


def _unit_labels(lang: str) -> tuple[str, str, str, str]:
    """Return the age words of a rail language as (now, minute, hour, day).

    Arguments:
        lang: The rail language, `en` or `ru`.

    Returns:
        The four words in the order the renderer uses them.

    Raises:
        ValueError: `lang` is neither `en` nor `ru`.
    """
    labels = _UNIT_LABELS.get(lang)
    if labels is None:
        raise ValueError(f"unknown rail language: {lang!r}")
    return labels


def _relative_age(labels: tuple[str, str, str, str], timestamp: float | None, now: float) -> str:
    """Render one age with the words of a language already resolved.

    Arguments:
        labels: The age words of the language, as `_unit_labels` returns them.
        timestamp: Epoch seconds of the message, or None when there is no message.
        now: The current time in epoch seconds.

    Returns:
        The formatted age, or the word for "now" when the timestamp is None or in the future.
    """
    now_word, minute, hour, day = labels
    if timestamp is None:
        return now_word
    seconds = now - timestamp
    if seconds < 60:  # a future timestamp lands here too: its negative delta is under a minute
        return now_word
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} {minute}"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} {hour}"
    return f"{hours // 24} {day}"


def format_relative(timestamp: float | None, now: float, lang: str) -> str:
    """Render how long ago a message arrived, in the language of the rail.

    The delta is `now - timestamp`, truncated to whole units: under 60 seconds renders as `now`
    (`en`) or `сейчас` (`ru`); under an hour as `{n} min` or `{n} мин`; under a day as `{n} h` or
    `{n} ч`; anything longer as `{n} d` or `{n} д`. No decimals, no plus sign.

    Arguments:
        timestamp: Epoch seconds of the message, or None when there is no message.
        now: The current time in epoch seconds.
        lang: The rail language, `en` or `ru`.

    Returns:
        The formatted age, or the word for "now" when the timestamp is None or in the future.

    Raises:
        ValueError: `lang` is neither `en` nor `ru`.
    """
    return _relative_age(_unit_labels(lang), timestamp, now)


def rail_payload(
    cards: Sequence[CardView],
    sessions: Sequence[SessionRow],
    seen: Mapping[str, float],
    now: float,
    lang: str,
) -> dict[str, Any]:
    """Build the exact JSON body of `POST /api/rail`.

    Sessions are ordered by `sort_sessions`, turned into views with `mark_unread` and given their
    `relative` age by `format_relative` with the given `now` and `lang`. Cards keep the order they
    arrive in, which is the board's order.

    Arguments:
        cards: The board cards of the project, in board order.
        sessions: The session rows, in any order.
        seen: The owner's last-look stamps, keyed by session id, in epoch seconds.
        now: The current time in epoch seconds.
        lang: The rail language, `en` or `ru`.

    Returns:
        `{"cards": [...], "sessions": [...], "counters": {...}}`, where every entry is a plain dict
        and the whole body passes `json.dumps`.

    Raises:
        ValueError: `lang` is neither `en` nor `ru`.
    """
    labels = _unit_labels(lang)
    ordered = [
        replace(item, relative=_relative_age(labels, item.last_message_at, now))
        for item in mark_unread(sort_sessions(sessions), seen)
    ]
    return {
        "cards": [
            {
                "id": item.id,
                "title": item.title,
                "labels": [list(label) for label in item.labels],
                "blocked": item.blocked,
                "url": item.url,
            }
            for item in cards
        ],
        "sessions": [asdict(item) for item in ordered],
        "counters": rail_counters(cards, ordered),
    }
