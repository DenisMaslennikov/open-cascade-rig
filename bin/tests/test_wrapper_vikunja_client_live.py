"""The same client against the real board, for the question the unit tests cannot answer.

Skipped unless `VIKUNJA_URL` is set, so the suite stays offline by default and CI never needs a board.
Run it on the machine that hosts Vikunja:

    VIKUNJA_URL=http://127.0.0.1:3457/api/v1 python3 -m pytest tests/test_wrapper_vikunja_client_live.py
"""

from __future__ import annotations

import os

import pytest
import vikunja_client as vk

pytestmark = pytest.mark.skipif(not os.environ.get("VIKUNJA_URL"), reason="set VIKUNJA_URL to read a real board")


def test_the_rig_board_shows_card_184_in_the_plan_column():
    cards = vk.VikunjaClient().plan_cards("cascade-rig")
    assert any(card.id == 184 for card in cards), [card.id for card in cards]
    assert {card.project_id for card in cards} == {7}


def test_every_card_of_the_plan_column_carries_a_link_and_a_blocker_state():
    for card in vk.VikunjaClient().plan_cards("cascade-rig"):
        assert card.url.endswith(f"/projects/7/tasks/{card.id}")
        assert isinstance(card.blocked, bool)
        assert all(label.title for label in card.labels)
