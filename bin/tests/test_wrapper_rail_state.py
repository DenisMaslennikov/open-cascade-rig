"""Behaviour contract for the rail's pure logic: order, unread marks, counters, ages, the JSON body.

Nothing here needs a clock, a database or a socket: every expected value comes from the rules the rail
is specified with, so a change in those rules has to be a change in this file.
"""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping, Sequence
from itertools import pairwise

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from tools.wrapper.rail_state import (
    CardView,
    SessionView,
    format_relative,
    mark_unread,
    rail_counters,
    rail_payload,
    sort_sessions,
)
from tools.wrapper.sessions_store import SessionRow

NOW = 1_700_000_000.0

RUSSIAN = {"now": "сейчас", "min": "мин", "h": "ч", "d": "д"}

AGES = [
    (None, "now", RUSSIAN["now"]),
    (-10, "now", RUSSIAN["now"]),
    (0, "now", RUSSIAN["now"]),
    (59, "now", RUSSIAN["now"]),
    (60, "1 min", f"1 {RUSSIAN['min']}"),
    (90, "1 min", f"1 {RUSSIAN['min']}"),
    (3599, "59 min", f"59 {RUSSIAN['min']}"),
    (3600, "1 h", f"1 {RUSSIAN['h']}"),
    (86399, "23 h", f"23 {RUSSIAN['h']}"),
    (86400, "1 d", f"1 {RUSSIAN['d']}"),
    (172800, "2 d", f"2 {RUSSIAN['d']}"),
]


def session(
    session_id: str,
    last_message_at: float | None,
    *,
    title: str = "",
    running: bool = False,
    message_count: int = 0,
) -> SessionRow:
    """Build a session row the way the store would report it."""
    return SessionRow(
        id=session_id,
        title=title or session_id,
        last_message_at=last_message_at,
        running=running,
        message_count=message_count,
    )


def view(
    session_id: str,
    *,
    last_message_at: float | None = None,
    running: bool = False,
    unread: bool = False,
) -> SessionView:
    """Build a session view the way the rail would serialise it."""
    return SessionView(
        id=session_id,
        title=session_id,
        last_message_at=last_message_at,
        running=running,
        unread=unread,
        relative="now",
    )


def card(
    card_id: int,
    *,
    title: str = "",
    labels: tuple[tuple[str, str], ...] = (),
    blocked: bool = False,
) -> CardView:
    """Build a card view the way the board client would report it."""
    return CardView(
        id=card_id,
        title=title or f"card {card_id}",
        labels=labels,
        blocked=blocked,
        url=f"http://board.test/tasks/{card_id}",
    )


def rail_order(rows: Sequence[SessionRow]) -> bool:
    """Say whether `rows` follow the rail rule: newest first, no message last, ties by id descending."""
    for left, right in pairwise(rows):
        left_key = (1, 0.0) if left.last_message_at is None else (0, -left.last_message_at)
        right_key = (1, 0.0) if right.last_message_at is None else (0, -right.last_message_at)
        if left_key > right_key:
            return False
        if left_key == right_key and left.id < right.id:
            return False
    return True


def unit_rank(text: str) -> tuple[int, int]:
    """Turn a rendered age into (unit, number) so two renderings can be compared."""
    if text in ("now", RUSSIAN["now"]):
        return (0, 0)
    number, _, unit = text.partition(" ")
    return ({"min": 1, "h": 2, "d": 3, RUSSIAN["min"]: 1, RUSSIAN["h"]: 2, RUSSIAN["d"]: 3}[unit], int(number))


def test_the_newest_message_comes_first() -> None:
    rows = [session("s-old", 100.0), session("s-new", 300.0), session("s-mid", 200.0)]

    assert [row.id for row in sort_sessions(rows)] == ["s-new", "s-mid", "s-old"]


def test_sessions_without_a_message_come_last() -> None:
    rows = [session("s-silent", None), session("s-old", 100.0)]

    assert [row.id for row in sort_sessions(rows)] == ["s-old", "s-silent"]


def test_equal_moments_are_settled_by_id_descending() -> None:
    rows = [session("s-a", 100.0), session("s-c", 100.0), session("s-b", 100.0)]

    assert [row.id for row in sort_sessions(rows)] == ["s-c", "s-b", "s-a"]


def test_sessions_without_a_message_are_settled_by_id_descending_too() -> None:
    rows = [session("s-a", None), session("s-b", None)]

    assert [row.id for row in sort_sessions(rows)] == ["s-b", "s-a"]


def test_sorting_leaves_the_input_list_alone() -> None:
    rows = [session("s-old", 100.0), session("s-new", 300.0)]
    untouched = list(rows)

    sort_sessions(rows)

    assert rows == untouched


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    rows=st.lists(
        st.builds(
            SessionRow,
            id=st.text(alphabet="abc", min_size=1, max_size=3),
            title=st.text(max_size=4),
            last_message_at=st.none() | st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
            running=st.booleans(),
            message_count=st.integers(min_value=0, max_value=20),
        ),
        max_size=6,
    )
)
def test_sorting_is_a_permutation_that_follows_the_rule_and_is_idempotent(rows: list[SessionRow]) -> None:
    once = sort_sessions(rows)

    assert Counter(once) == Counter(rows)
    assert rail_order(once)
    assert sort_sessions(once) == once


def test_a_message_newer_than_the_seen_stamp_is_unread() -> None:
    rows = [session("s1", 200.0)]

    assert [item.unread for item in mark_unread(rows, {"s1": 100.0})] == [True]


def test_a_message_at_the_seen_stamp_is_read() -> None:
    rows = [session("s1", 200.0)]

    assert [item.unread for item in mark_unread(rows, {"s1": 200.0})] == [False]


def test_a_session_never_looked_at_is_unread() -> None:
    rows = [session("s1", 10.0)]

    assert [item.unread for item in mark_unread(rows, {})] == [True]


def test_a_session_without_a_message_is_never_unread() -> None:
    rows = [session("s1", None)]

    assert [item.unread for item in mark_unread(rows, {"s1": 1_700_000_000.0})] == [False]


def test_marking_unread_carries_every_row_field_and_keeps_the_order() -> None:
    rows = [
        session("s-new", 300.0, title="Newest", running=True, message_count=4),
        session("s-old", 100.0),
    ]

    marked = mark_unread(rows, {"s-new": 200.0})

    assert [(item.id, item.title, item.last_message_at, item.running) for item in marked] == [
        ("s-new", "Newest", 300.0, True),
        ("s-old", "s-old", 100.0, False),
    ]


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    rows=st.lists(
        st.builds(
            SessionRow,
            id=st.text(alphabet="abc", min_size=1, max_size=3),
            title=st.text(max_size=4),
            last_message_at=st.none() | st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
            running=st.booleans(),
            message_count=st.integers(min_value=0, max_value=20),
        ),
        max_size=6,
    ),
    seen=st.dictionaries(
        st.text(alphabet="abc", min_size=1, max_size=3),
        st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
        max_size=4,
    ),
)
def test_marking_unread_never_reorders_the_sessions(rows: list[SessionRow], seen: Mapping[str, float]) -> None:
    marked = mark_unread(rows, seen)

    assert [item.id for item in marked] == [row.id for row in rows]
    for row, item in zip(rows, marked, strict=True):
        expected = row.last_message_at is not None and row.last_message_at > seen.get(row.id, 0.0)
        assert item.unread == expected


def test_counters_report_cards_running_sessions_and_unread_sessions() -> None:
    cards = [card(1), card(2), card(3)]
    sessions = [
        view("s1", last_message_at=100.0, running=True, unread=True),
        view("s2", last_message_at=200.0, running=True),
        view("s3", last_message_at=None),
    ]

    assert rail_counters(cards, sessions) == {"cards": 3, "sessions": 2, "unread": 1}


def test_counters_of_an_empty_rail_are_zero() -> None:
    assert rail_counters([], []) == {"cards": 0, "sessions": 0, "unread": 0}


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    cards=st.lists(
        st.builds(
            CardView,
            id=st.integers(),
            title=st.text(max_size=4),
            labels=st.just(()),
            blocked=st.booleans(),
            url=st.just("http://board.test"),
        ),
        max_size=5,
    ),
    sessions=st.lists(
        st.builds(
            SessionView,
            id=st.text(alphabet="abc", min_size=1, max_size=3),
            title=st.text(max_size=4),
            last_message_at=st.none() | st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
            running=st.booleans(),
            unread=st.booleans(),
            relative=st.just("now"),
        ),
        max_size=5,
    ),
)
def test_counters_never_exceed_the_lists_they_count(cards: list[CardView], sessions: list[SessionView]) -> None:
    counters = rail_counters(cards, sessions)

    assert counters["cards"] == len(cards)
    assert counters["sessions"] <= len(sessions)
    assert counters["unread"] <= len(sessions)
    assert set(counters) == {"cards", "sessions", "unread"}


@pytest.mark.parametrize(("delta", "english", "russian"), AGES)
@pytest.mark.parametrize("lang", ["en", "ru"])
def test_ages_are_rendered_in_whole_units_of_the_language(
    delta: int | None, english: str, russian: str, lang: str
) -> None:
    timestamp = None if delta is None else NOW - delta

    assert format_relative(timestamp, NOW, lang) == (english if lang == "en" else russian)


@pytest.mark.parametrize("lang", ["fr", "", "EN", "ru-RU"])
def test_an_unknown_language_is_rejected(lang: str) -> None:
    with pytest.raises(ValueError):
        format_relative(NOW, NOW, lang)


@pytest.mark.property
@settings(max_examples=50, deadline=None)
@given(
    first=st.integers(min_value=0, max_value=10_000_000),
    second=st.integers(min_value=0, max_value=10_000_000),
)
@pytest.mark.parametrize("lang", ["en", "ru"])
def test_a_longer_age_never_renders_as_a_smaller_unit(first: int, second: int, lang: str) -> None:
    shorter, longer = min(first, second), max(first, second)

    assert unit_rank(format_relative(NOW - shorter, NOW, lang)) <= unit_rank(format_relative(NOW - longer, NOW, lang))


def test_the_payload_is_the_json_body_of_the_rail() -> None:
    cards = [
        card(7, title="Fix the rail", labels=(("bug", "#e5534b"),), blocked=False),
        card(9, title="Ship the rail", blocked=True),
    ]
    sessions = [
        session("s-a", 100.0, title="A", running=True, message_count=2),
        session("s-b", None, title="B"),
        session("s-c", 200.0, title="C", running=True, message_count=5),
    ]

    body = rail_payload(cards, sessions, {"s-a": 150.0, "s-c": 100.0}, 200.0, "en")

    assert json.loads(json.dumps(body)) == {
        "cards": [
            {
                "id": 7,
                "title": "Fix the rail",
                "labels": [["bug", "#e5534b"]],
                "blocked": False,
                "url": "http://board.test/tasks/7",
            },
            {
                "id": 9,
                "title": "Ship the rail",
                "labels": [],
                "blocked": True,
                "url": "http://board.test/tasks/9",
            },
        ],
        "sessions": [
            {
                "id": "s-c",
                "title": "C",
                "last_message_at": 200.0,
                "running": True,
                "unread": True,
                "relative": "now",
            },
            {
                "id": "s-a",
                "title": "A",
                "last_message_at": 100.0,
                "running": True,
                "unread": False,
                "relative": "1 min",
            },
            {
                "id": "s-b",
                "title": "B",
                "last_message_at": None,
                "running": False,
                "unread": False,
                "relative": "now",
            },
        ],
        "counters": {"cards": 2, "sessions": 2, "unread": 1},
    }


def test_the_payload_renders_ages_in_the_requested_language() -> None:
    body = rail_payload([card(1)], [session("s1", NOW - 7200, title="A")], {}, NOW, "ru")

    assert body["sessions"][0]["relative"] == f"2 {RUSSIAN['h']}"


@pytest.mark.property
@settings(max_examples=25, deadline=None)
@given(
    cards=st.lists(st.integers(min_value=0, max_value=5), max_size=4),
    sessions=st.lists(
        st.tuples(
            st.text(alphabet="abc", min_size=1, max_size=3),
            st.none() | st.floats(min_value=0, max_value=10_000, allow_nan=False, allow_infinity=False),
            st.booleans(),
        ),
        max_size=4,
    ),
)
def test_payload_counters_never_exceed_the_lists(
    cards: list[int], sessions: list[tuple[str, float | None, bool]]
) -> None:
    body = rail_payload(
        [card(card_id) for card_id in cards],
        [session(session_id, moment, running=running) for session_id, moment, running in sessions],
        {},
        NOW,
        "en",
    )

    assert body["counters"]["cards"] == len(cards)
    assert body["counters"]["sessions"] <= len(sessions)
    assert body["counters"]["unread"] <= len(sessions)
    assert len(body["sessions"]) == len(sessions)
