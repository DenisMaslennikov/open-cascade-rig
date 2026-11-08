"""Behaviour of the rail composition module: the application factory and the command-line entry.

Every boundary — the rail database, the board and dashboard sources, the server factory and uvicorn —
is replaced, so no test opens a socket, a database file or a network connection.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from tools.wrapper import rail_app


def _board_source(slug: str) -> tuple[object, ...]:
    """Answer the cards of one project slug; these tests hand over no cards."""
    del slug
    return ()


def _dashboard_source(limit: int) -> tuple[object, ...]:
    """Answer at most `limit` recent sessions; these tests hand over none."""
    del limit
    return ()


def _forwarded(call: Any) -> list[Any]:
    """Return the positional arguments and keyword values of a recorded call in one list.

    Arguments:
        call: The `call_args` of a mock, or None when the mock was never called.

    Returns:
        Every positional argument followed by every keyword value.
    """
    if call is None:
        return []
    args, kwargs = call
    return [*args, *kwargs.values()]


def _stub_rail_database(monkeypatch: pytest.MonkeyPatch) -> tuple[MagicMock, MagicMock]:
    """Replace the rail database and the seen store, so nothing is opened on disk.

    Arguments:
        monkeypatch: The pytest patcher of the running test.

    Returns:
        The session-factory stub and the seen-store stub, in that order.
    """
    factory = MagicMock(return_value=MagicMock())
    seen_store = MagicMock()
    monkeypatch.setattr(rail_app, "session_factory", factory)
    monkeypatch.setattr(rail_app, "SeenStore", seen_store)
    return factory, seen_store


def test_the_application_built_is_the_one_the_server_module_returns(tmp_path, monkeypatch):
    artefacts = tmp_path / "artefacts"
    artefacts.mkdir()
    application = object()
    create_app = MagicMock(return_value=application)
    monkeypatch.setattr(rail_app, "create_app", create_app)
    _stub_rail_database(monkeypatch)

    built = rail_app.build_app(tmp_path / "rail.sqlite3", artefacts, _board_source, _dashboard_source)

    assert built is application
    create_app.assert_called_once()


def test_the_board_and_dashboard_sources_reach_the_application_unchanged(tmp_path, monkeypatch):
    artefacts = tmp_path / "artefacts"
    artefacts.mkdir()
    create_app = MagicMock(return_value=object())
    monkeypatch.setattr(rail_app, "create_app", create_app)
    _stub_rail_database(monkeypatch)

    rail_app.build_app(tmp_path / "rail.sqlite3", artefacts, _board_source, _dashboard_source)

    forwarded = _forwarded(create_app.call_args)
    assert _board_source in forwarded
    assert _dashboard_source in forwarded


def test_the_seen_store_is_opened_on_the_rail_database_it_was_given(tmp_path, monkeypatch):
    artefacts = tmp_path / "artefacts"
    artefacts.mkdir()
    monkeypatch.setattr(rail_app, "create_app", MagicMock(return_value=object()))
    factory, seen_store = _stub_rail_database(monkeypatch)
    db_path = tmp_path / "rail.sqlite3"

    rail_app.build_app(db_path, artefacts, _board_source, _dashboard_source)

    assert db_path in _forwarded(factory.call_args)
    assert factory.return_value in _forwarded(seen_store.call_args)


def test_a_missing_artefacts_directory_is_rejected_before_the_application_is_built(tmp_path, monkeypatch):
    create_app = MagicMock(return_value=object())
    monkeypatch.setattr(rail_app, "create_app", create_app)
    _stub_rail_database(monkeypatch)

    with pytest.raises(FileNotFoundError):
        rail_app.build_app(tmp_path / "rail.sqlite3", tmp_path / "absent", _board_source, _dashboard_source)

    create_app.assert_not_called()


def test_the_database_option_has_no_default(monkeypatch):
    run = MagicMock()
    monkeypatch.setattr(rail_app.uvicorn, "run", run)

    with pytest.raises(SystemExit) as exit_info:
        rail_app.main([])

    assert exit_info.value.code == 2
    run.assert_not_called()


def test_pending_migrations_run_before_the_server_starts(tmp_path, monkeypatch):
    steps: list[str] = []
    application = object()

    def migrate(*args: Any, **kwargs: Any) -> None:
        steps.append("migrate")

    def build(*args: Any, **kwargs: Any) -> object:
        steps.append("build")
        return application

    def serve(*args: Any, **kwargs: Any) -> None:
        steps.append("serve")

    migration = MagicMock(side_effect=migrate)
    serve_mock = MagicMock(side_effect=serve)
    monkeypatch.setattr(rail_app, "apply_pending_migrations", migration)
    monkeypatch.setattr(rail_app, "build_app", MagicMock(side_effect=build))
    monkeypatch.setattr(rail_app, "VikunjaClient", MagicMock())
    monkeypatch.setattr(rail_app, "SessionsStore", MagicMock())
    monkeypatch.setattr(rail_app.uvicorn, "run", serve_mock)

    db_path = tmp_path / "rail.sqlite3"
    code = rail_app.main(["--db", str(db_path)])

    assert code == 0
    assert "migrate" in steps
    assert "serve" in steps
    assert steps.index("migrate") < steps.index("serve")
    assert str(db_path) in [str(value) for value in _forwarded(migration.call_args)]
    serve_mock.assert_called_once()
    assert application in _forwarded(serve_mock.call_args)


@given(argv=st.lists(st.text(max_size=8), max_size=4))
@settings(max_examples=25, deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_arguments_without_the_database_option_never_start_the_server(argv, monkeypatch):
    assume(not any(argument.startswith("-") for argument in argv))
    run = MagicMock()
    monkeypatch.setattr(rail_app.uvicorn, "run", run)
    monkeypatch.setattr(rail_app, "apply_pending_migrations", MagicMock())

    with pytest.raises(SystemExit) as exit_info:
        rail_app.main(argv)

    assert exit_info.value.code == 2
    run.assert_not_called()
