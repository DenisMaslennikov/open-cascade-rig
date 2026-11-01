"""Behaviour of the wrapper's read-only Vikunja client, proven without a board and without a network.

The card list is the wrapper's only source of truth about what can be started, so these tests pin
the three rules the card names: a project is selected by slug, only cards of the `Plan` column are
shown, and a card is blocked when it waits for another card that is not done or carries the
`blocked` label.
"""

from __future__ import annotations

import importlib
import json
import re
import sys
from pathlib import Path

import pytest
import vikunja_client as vk
from hypothesis import given
from hypothesis import strategies as st

BASE = "http://board.test/api/v1"
PROJECT_TITLE = "cascade-rig — отладка мультиагентного конвейера"


class FakeTransport:
    """Serves canned payloads by (method, path) and records what the client asked for.

    The query string is dropped from the key on purpose: the tests speak about endpoints, not about
    the page size the client happens to use.
    """

    def __init__(self, routes: dict[tuple[str, str], tuple[int, object]]):
        self.routes = routes
        self.calls: list[tuple[str, str, str | None]] = []

    def request(self, method: str, path: str, body=None, token=None):
        self.calls.append((method, path.split("?")[0], token))
        return self.routes.get((method, path.split("?")[0]), (404, {"message": "Not Found"}))


def card(
    cid: int,
    title: str = "",
    labels: tuple[tuple[str, str], ...] = (),
    blocked: tuple[tuple[int, bool], ...] = (),
    project_id: int = 7,
) -> dict:
    """One task as the board returns it: `blocked` is (id, done) of each relation that blocks it."""
    return {
        "id": cid,
        "title": title or f"card {cid}",
        "project_id": project_id,
        "labels": [{"title": title_, "hex_color": colour} for title_, colour in labels],
        "related_tasks": {
            "blocked": [{"id": other, "title": f"card {other}", "done": done} for other, done in blocked]
        },
    }


def routes(
    plan: tuple[dict, ...] = (),
    backlog: tuple[dict, ...] = (),
    done: tuple[dict, ...] = (),
    plan_column: str = "Plan",
    project_title: str = PROJECT_TITLE,
) -> dict[tuple[str, str], tuple[int, object]]:
    """A board with one project, one kanban view and the usual columns."""
    buckets = [
        {"id": 47, "title": "Backlog", "tasks": list(backlog)},
        {"id": 48, "title": plan_column, "tasks": list(plan)},
        {"id": 54, "title": "Done", "tasks": list(done)},
    ]
    return {
        ("GET", "/projects"): (
            200,
            [
                {"id": 7, "title": project_title, "is_archived": False},
                {"id": 9, "title": "Old project — archived", "is_archived": True},
            ],
        ),
        ("GET", "/projects/7/views"): (
            200,
            [
                {"id": 27, "title": "List", "view_kind": "list"},
                {"id": 30, "title": "Kanban", "view_kind": "kanban"},
            ],
        ),
        ("GET", "/projects/7/views/30/tasks"): (200, buckets),
    }


def client(routes_: dict, **kwargs) -> vk.VikunjaClient:
    return vk.VikunjaClient(BASE, token="test-token", transport=FakeTransport(routes_), **kwargs)


def test_the_module_is_importable_through_the_package_path_the_rail_task_uses(repo_root):
    """Subtask 184.2 imports `tools.wrapper.vikunja_client` from the repository root; a rename or a
    wrapper `__init__.py` that changes the path would break it without any test noticing."""
    root = str(repo_root)
    sys.path.insert(0, root)
    try:
        module = importlib.import_module("tools.wrapper.vikunja_client")
    finally:
        sys.path.remove(root)
    assert Path(str(module.__file__)).resolve() == Path(str(vk.__file__)).resolve()
    assert module.VikunjaClient.__name__ == "VikunjaClient"


# --------------------------------------------------------------------------- projects by slug


def test_project_slug_is_the_short_name_before_the_dash():
    assert vk.project_slug("cascade-rig — отладка мультиагентного конвейера") == "cascade-rig"
    assert vk.project_slug("OCCC — OpenCapsuleerCommandCenter") == "occc"
    assert vk.project_slug("Inbox") == "inbox"
    assert vk.project_slug("  spaced  ") == "spaced"


@pytest.mark.property
@given(st.text(max_size=60))
def test_project_slug_is_a_lowercase_ascii_slug_for_every_title(title: str):
    slug = vk.project_slug(title)
    assert re.fullmatch(r"[a-z0-9](?:[a-z0-9-]*[a-z0-9])?", slug), repr(slug)


def test_projects_are_listed_by_slug_and_archived_ones_are_skipped():
    projects = client(routes()).projects()
    assert [(p.id, p.slug) for p in projects] == [(7, "cascade-rig")]


def test_a_project_is_found_by_slug_and_by_number():
    board = client(routes())
    assert board.project("cascade-rig").id == 7
    assert board.project("CASCADE-RIG").id == 7
    assert board.project(7).id == 7
    assert board.project("7").id == 7


def test_an_unknown_project_names_the_slugs_that_exist():
    with pytest.raises(vk.UnknownProject) as error:
        client(routes()).project("occc")
    assert "cascade-rig" in str(error.value)


# --------------------------------------------------------------------------- cards of Plan


def test_only_the_plan_column_is_shown():
    board = client(
        routes(
            plan=(card(184, "web wrapper"), card(185, "reasoning tokens")),
            backlog=(card(155, "adr split"),),
            done=(card(186, "gates port"),),
        )
    )
    assert [c.id for c in board.plan_cards("cascade-rig")] == [184, 185]


def test_card_labels_carry_their_title_and_colour():
    board = client(routes(plan=(card(184, labels=(("rig · tech debt", "b07a3c"),)),)))
    (only,) = board.plan_cards("cascade-rig")
    assert [(label.title, label.colour) for label in only.labels] == [("rig · tech debt", "b07a3c")]


def test_a_card_without_labels_or_relations_is_read_without_them():
    board = client(routes(plan=({"id": 186, "title": "no labels", "labels": None, "related_tasks": {}},)))
    (only,) = board.plan_cards("cascade-rig")
    assert (only.labels, only.blockers, only.blocked) == ((), (), False)


def test_a_missing_plan_column_is_reported_with_the_columns_that_exist():
    board = client(routes(plan_column="Queue"))
    with pytest.raises(vk.VikunjaError) as error:
        board.plan_cards("cascade-rig")
    assert "Queue" in str(error.value)


def test_the_card_link_points_at_the_board_interface():
    board = client(routes(plan=(card(184),)))
    (only,) = board.plan_cards("cascade-rig")
    assert only.url == "http://board.test/projects/7/tasks/184"


# --------------------------------------------------------------------------- the blocker flag


def test_a_card_waits_for_an_unfinished_blocker():
    board = client(routes(plan=(card(184, blocked=((144, False),)),)))
    (only,) = board.plan_cards("cascade-rig")
    assert only.blocked is True
    assert [(b.card_id, b.done) for b in only.blockers] == [(144, False)]


def test_finished_blockers_leave_the_card_free():
    board = client(routes(plan=(card(184, blocked=((144, True), (145, True))),)))
    (only,) = board.plan_cards("cascade-rig")
    assert only.blocked is False


def test_a_real_payload_with_two_unfinished_blockers_marks_the_card_blocked():
    """Captured from the live board: card 150 of OCCC waits for 144 and 146, and also carries
    `blocking` and `related` relations, which are not the card's own blockers."""
    fixture = json.loads(
        (Path(__file__).parent / "fixtures" / "vikunja_task_with_blockers.json").read_text(encoding="utf-8")
    )
    (only,) = client(routes(plan=(fixture,))).plan_cards("cascade-rig")
    assert only.blocked is True
    assert [blocker.card_id for blocker in only.blockers] == [144, 146]
    assert (only.labels[0].title, only.labels[0].colour) == ("OCCC · SCHEMA", "a0a0a0")


def test_the_blocked_label_marks_a_card_by_itself():
    board = client(routes(plan=(card(184, labels=(("Blocked", "c0392b"),)),)))
    (only,) = board.plan_cards("cascade-rig")
    assert only.blocked is True


@pytest.mark.property
@given(st.lists(st.text(max_size=20), max_size=4))
def test_an_unfinished_blocker_always_blocks_whatever_the_labels_say(titles: list[str]):
    labels = tuple((title, "a0a0a0") for title in titles if title.strip().casefold() != "blocked")
    board = client(routes(plan=(card(184, labels=labels, blocked=((144, False),)),)))
    (only,) = board.plan_cards("cascade-rig")
    assert only.blocked is True


@pytest.mark.property
@given(st.one_of(st.none(), st.text(max_size=10), st.lists(st.integers()), st.dictionaries(st.text(), st.text())))
def test_a_relation_payload_of_the_wrong_shape_does_not_break_the_card(payload: object):
    board = client(routes(plan=({"id": 184, "title": "odd card", "labels": None, "related_tasks": payload},)))
    (only,) = board.plan_cards("cascade-rig")
    assert only.blocked is False


# --------------------------------------------------------------------------- transport and config


def test_http_error_names_the_method_the_path_and_the_status():
    board = client({})
    with pytest.raises(vk.VikunjaHTTPError) as error:
        board.projects()
    assert error.value.method == "GET"
    assert error.value.path.startswith("/projects")
    assert error.value.status == 404


def test_the_token_comes_from_the_environment_when_none_is_passed(monkeypatch):
    monkeypatch.setenv("VIKUNJA_TOKEN", "from-environment")
    transport = FakeTransport(routes())
    board = vk.VikunjaClient(BASE, transport=transport)
    board.projects()
    assert transport.calls[-1][2] == "from-environment"


def test_a_client_without_a_base_url_says_where_to_put_one(monkeypatch, tmp_path):
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    with pytest.raises(vk.VikunjaConfigError) as error:
        vk.VikunjaClient(creds_path=tmp_path / "no-creds.json")
    assert "VIKUNJA_URL" in str(error.value)


def test_the_login_fallback_uses_the_credentials_file(monkeypatch, tmp_path):
    monkeypatch.delenv("VIKUNJA_URL", raising=False)
    monkeypatch.delenv("VIKUNJA_TOKEN", raising=False)
    creds = tmp_path / "creds.json"
    creds.write_text(json.dumps({"base": BASE, "username": "oswe", "password": "pw"}))
    transport = FakeTransport({("POST", "/login"): (200, {"token": "from-login"}), **routes()})
    board = vk.VikunjaClient(transport=transport, creds_path=creds)
    board.projects()
    assert transport.calls[0][:2] == ("POST", "/login")
    assert transport.calls[-1][2] == "from-login"


def test_pages_are_walked_until_a_page_brings_no_new_card():
    first = [card(184), card(185)]
    second = [card(184), card(186)]

    class PagedTransport(FakeTransport):
        def request(self, method: str, path: str, body=None, token=None):
            if "&page=1" in path:
                return 200, [
                    {"id": 48, "title": "Plan", "tasks": first},
                    {"id": 47, "title": "Backlog", "tasks": []},
                ]
            if "&page=" in path:
                return 200, [
                    {"id": 48, "title": "Plan", "tasks": second},
                    {"id": 47, "title": "Backlog", "tasks": []},
                ]
            return super().request(method, path, body, token)

    transport = PagedTransport(routes())
    board = vk.VikunjaClient(BASE, token="test-token", transport=transport)
    assert [c.id for c in board.plan_cards(7)] == [184, 185, 186]
