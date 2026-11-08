"""The defects an independent review found in the composition layer, pinned so they cannot come back.

Each test failed against the code as first committed: the board's `Card` reached `rail_payload` with
`Label` objects and crashed it outside the error mapping (500 for every real request); an unknown
language was not validated and reached the same crash; an unknown board project answered 502 instead of
400; a NUL byte in a static path raised out of `Path.resolve` as 500 instead of 404; and the seen store
was constructed and thrown away, so the rail database was migrated for nothing.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from tools.wrapper import rail_app
from tools.wrapper.rail_server import create_app
from tools.wrapper.rail_state import CardView
from tools.wrapper.vikunja_client import Card, Label, UnknownProject


def _card(card_id: int = 1) -> Card:
    """One board card as the Vikunja client returns it, labels as objects."""
    return Card(
        id=card_id,
        title="Rail",
        project_id=7,
        labels=(Label(title="ui", colour="#5b8ff9"),),
        blockers=(),
        url=f"http://board.test/tasks/{card_id}",
    )


def _static_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "artefacts"
    directory.mkdir()
    (directory / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return directory


def test_a_board_card_becomes_a_card_view_with_label_pairs():
    """`labels` must be (title, colour) pairs: `Label` objects reach `rail_payload` as a TypeError."""
    views = rail_app.card_views([_card(3)])

    assert views == [
        CardView(id=3, title="Rail", labels=(("ui", "#5b8ff9"),), blocked=False, url="http://board.test/tasks/3")
    ]


def test_an_unknown_board_project_answers_400_not_502(tmp_path: Path):
    """The board client's own `UnknownProject` must reach the client as "no such project"."""
    client = MagicMock()
    client.plan_cards.side_effect = UnknownProject("no project 'nope'")
    app = create_app(rail_app.board_reader(client), lambda limit: (), _static_dir(tmp_path))

    response = TestClient(app).post("/api/rail", json={"project": "nope", "seen": {}, "lang": "en"})

    assert response.status_code == 400


def test_a_real_board_card_answers_200_through_the_application(tmp_path: Path):
    """The end-to-end shape of the bug: a real card used to crash the payload with a 500."""
    client = MagicMock()
    client.plan_cards.return_value = [_card()]
    app = create_app(rail_app.board_reader(client), lambda limit: (), _static_dir(tmp_path))

    response = TestClient(app).post("/api/rail", json={"project": "cascade-rig", "seen": {}, "lang": "en"})

    assert response.status_code == 200
    assert response.json()["cards"][0]["labels"] == [["ui", "#5b8ff9"]]


@pytest.mark.parametrize("lang", ["fr", "", "EN", "ru-RU"])
def test_an_unknown_language_answers_400(tmp_path: Path, lang: str):
    """A language outside `en|ru` is a client error, never a 500 from inside `rail_payload`."""
    app = create_app(lambda project: (), lambda limit: (), _static_dir(tmp_path))

    response = TestClient(app).post("/api/rail", json={"project": "cascade-rig", "seen": {}, "lang": lang})

    assert response.status_code == 400


def test_a_null_byte_in_a_static_path_answers_404(tmp_path: Path):
    """`Path.resolve` raises on a NUL byte; the contract says "no such file", not 500."""
    app = create_app(lambda project: (), lambda limit: (), _static_dir(tmp_path))

    response = TestClient(app).get("/index%00.html")

    assert response.status_code == 404


def test_the_injected_seen_store_records_the_posted_marks(tmp_path: Path):
    """The rail database is only worth migrating if the marks are actually written."""
    seen_store = MagicMock()
    seen_store.store_seen = MagicMock()
    app = create_app(lambda project: (), lambda limit: (), _static_dir(tmp_path), seen_store)

    TestClient(app).post("/api/rail", json={"project": "cascade-rig", "seen": {"s-1": 1700000000}, "lang": "en"})

    seen_store.store_seen.assert_called_once_with("cascade-rig", {"s-1": 1700000000.0})


def test_the_cards_source_answers_in_board_order():
    """Order is the board's, not ours: the rail shows the cards as the board lists them."""
    views: Sequence[CardView] = rail_app.card_views([_card(2), _card(1)])

    assert [view.id for view in views] == [2, 1]
