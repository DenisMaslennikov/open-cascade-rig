"""The reviewer binary is found through configuration, never through a path baked into the module.

Card #207: `pr_review.py` held the reviewer at `~/.hermes/cache/scratch/pragent-venv/bin/pr-agent`.
Hermes prunes that cache after a day of idleness, so `Popen` raised FileNotFoundError, the run
record stayed `dead`, and the review phase vanished without a message. The resolver must read the
project config, then `CASCADE_REVIEW_BINARY`, and fail with an instruction naming `reviewer_binary`
when neither yields a usable file. A configured path that does not exist is an error naming that
path, never a silent fall-through.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rig import plan_task, pr_review, rig_conf
from rig.pr_review import pr_agent_binary


@pytest.fixture
def cascade_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A private XDG config root, wired into every module that cached its config path at import."""
    config_home = tmp_path / "config"
    cascade = config_home / "cascade"
    (cascade / "projects").mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("CASCADE_REVIEW_BINARY", raising=False)
    monkeypatch.setattr(rig_conf, "CONFIG", cascade)
    monkeypatch.setattr(plan_task, "CONFIG", cascade)
    monkeypatch.setattr(pr_review, "CONFIG", cascade, raising=False)
    return cascade


def _executable(directory: Path, name: str = "pr-agent") -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / name
    binary.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    binary.chmod(0o755)
    return binary


def _write_project_config(cascade: Path, slug: str, body: str) -> None:
    (cascade / "projects" / f"{slug}.yaml").write_text(body, encoding="utf-8")


def test_the_project_config_wins_over_the_environment(cascade_config, tmp_path, monkeypatch):
    configured = _executable(tmp_path / "configured")
    from_environment = _executable(tmp_path / "environment")
    _write_project_config(cascade_config, "demo", f"reviewer_binary: {configured}\n")
    monkeypatch.setenv("CASCADE_REVIEW_BINARY", str(from_environment))

    assert pr_agent_binary("demo") == configured


def test_the_environment_is_used_when_the_project_config_has_no_key(cascade_config, tmp_path, monkeypatch):
    from_environment = _executable(tmp_path / "environment")
    _write_project_config(cascade_config, "demo", "models:\n  reviewer: cheap-model\n")
    monkeypatch.setenv("CASCADE_REVIEW_BINARY", str(from_environment))

    assert pr_agent_binary("demo") == from_environment


def test_a_configured_binary_that_is_missing_is_an_error_naming_that_path(cascade_config, tmp_path, monkeypatch):
    missing = tmp_path / "gone" / "pr-agent"
    fallback = _executable(tmp_path / "environment")
    _write_project_config(cascade_config, "demo", f"reviewer_binary: {missing}\n")
    monkeypatch.setenv("CASCADE_REVIEW_BINARY", str(fallback))

    with pytest.raises(SystemExit) as raised:
        pr_agent_binary("demo")

    message = str(raised.value)
    assert str(missing) in message
    assert "reviewer_binary" in message


def test_nothing_configured_names_the_key_the_project_and_the_setup_tool(cascade_config, monkeypatch):
    monkeypatch.delenv("CASCADE_REVIEW_BINARY", raising=False)

    with pytest.raises(SystemExit) as raised:
        pr_agent_binary("demo")

    message = str(raised.value)
    assert "reviewer_binary" in message
    assert "demo" in message
    assert "setup_pr_agent.py" in message
    assert "Traceback" not in message


def test_the_module_holds_no_reviewer_path_under_the_hermes_cache():
    for name, value in vars(pr_review).items():
        if isinstance(value, Path):
            assert ".hermes/cache" not in str(value), f"pr_review.{name} is {value}"
