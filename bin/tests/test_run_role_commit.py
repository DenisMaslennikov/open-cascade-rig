"""A run is settled into a commit or into a reason, never into a silent failure.

`run_role` commits the run itself once the acceptance passed. A role that already committed its own
work leaves a clean tree, so `git commit` answers "nothing to commit", the run reports
`commit: null` together with an empty `error`, and `record.json` says "failed" although the work
sits in a commit on the branch.

These tests freeze the decision as one pure function: given the worktree, the base commit, whether
the acceptance passed and the message to use, the answer is either a commit that resolves in the
repository or a non-empty reason. Every case runs in a throwaway repository under `tmp_path`, so the
repository under test is never touched.
"""

from __future__ import annotations

import itertools
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from rig.run_role import settle_worktree

MESSAGE = "executor: leave the work in one commit"

HOOK_REASON = "the fixture hook refuses the commit"

UNCHANGED = "the role changed nothing"
UNCOMMITTED = "the role left its work uncommitted"
SELF_COMMITTED = "the role committed its own work"
SCENARIOS = (UNCHANGED, UNCOMMITTED, SELF_COMMITTED)

MESSAGES = st.from_regex(r"[a-z][a-z0-9 :._-]{0,39}", fullmatch=True)

_DIRS = itertools.count()


@pytest.fixture(autouse=True)
def _isolated_git_config(tmp_path, monkeypatch):
    """Make git read the fixture's repository config only, never the machine's."""
    empty = tmp_path / "empty-gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "0")
    monkeypatch.setenv("LC_ALL", "C")


def _git(worktree: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=worktree, capture_output=True, text=True, check=False)


def _repository(root: Path) -> Path:
    """A repository with one base commit and a configuration of its own, so commits resolve."""
    worktree = root / f"worktree{next(_DIRS)}"
    worktree.mkdir(parents=True)
    assert _git(worktree, "init", "-b", "main", "-q").returncode == 0
    for key, value in (("user.name", "cascade-test"), ("user.email", "cascade@localhost"), ("commit.gpgsign", "false")):
        assert _git(worktree, "config", key, value).returncode == 0
    (worktree / "base.txt").write_text("base\n", encoding="utf-8")
    assert _git(worktree, "add", "-A").returncode == 0
    assert _git(worktree, "commit", "-m", "the base commit").returncode == 0
    return worktree


def _head(worktree: Path) -> str:
    return _git(worktree, "rev-parse", "HEAD").stdout.strip()


def _status(worktree: Path) -> str:
    return _git(worktree, "status", "--porcelain").stdout


def _commit_count(worktree: Path) -> int:
    return int(_git(worktree, "rev-list", "--count", "HEAD").stdout.strip())


def _leave_uncommitted_work(worktree: Path) -> None:
    (worktree / "work.txt").write_text("done\n", encoding="utf-8")


def _commit_itself(worktree: Path) -> None:
    _leave_uncommitted_work(worktree)
    assert _git(worktree, "add", "-A").returncode == 0
    assert _git(worktree, "commit", "-m", "executor: the role's own commit").returncode == 0


def test_a_role_that_committed_itself_is_reported_done_with_that_commit(tmp_path):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    _commit_itself(worktree)
    head_before = _head(worktree)
    commits_before = _commit_count(worktree)
    assert head_before != base

    result = settle_worktree(worktree, base, True, MESSAGE)

    assert result["ok"] is True
    assert result["commit"] == head_before
    assert result["error"] is None
    assert _head(worktree) == head_before
    assert _commit_count(worktree) == commits_before


def test_uncommitted_work_is_committed_with_the_message_given(tmp_path):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    _leave_uncommitted_work(worktree)

    result = settle_worktree(worktree, base, True, MESSAGE)

    assert result["ok"] is True
    assert result["error"] is None
    assert isinstance(result["commit"], str)
    assert len(result["commit"]) == 40
    assert _git(worktree, "rev-parse", "--verify", f"{result['commit']}^{{commit}}").stdout.strip() == result["commit"]
    assert _status(worktree) == ""
    assert _git(worktree, "log", "-1", "--pretty=%s").stdout.strip() == MESSAGE


def test_a_run_that_changed_nothing_fails_with_a_reason(tmp_path):
    worktree = _repository(tmp_path)
    base = _head(worktree)

    result = settle_worktree(worktree, base, True, MESSAGE)

    assert result["ok"] is False
    assert result["commit"] is None
    assert isinstance(result["error"], str)
    assert result["error"].strip()
    assert _head(worktree) == base


def test_a_failed_acceptance_commits_nothing(tmp_path):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    _leave_uncommitted_work(worktree)
    dirty_before = _status(worktree)
    assert dirty_before.strip()

    result = settle_worktree(worktree, base, False, MESSAGE)

    assert result["ok"] is False
    assert result["commit"] is None
    assert isinstance(result["error"], str)
    assert result["error"].strip()
    assert "acceptance" in result["error"].lower()
    assert _head(worktree) == base
    assert _status(worktree) == dirty_before


def test_a_commit_refused_by_hooks_is_reported_with_the_reason_git_relays(tmp_path):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    hook = worktree / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho '{HOOK_REASON}' >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    _leave_uncommitted_work(worktree)

    result = settle_worktree(worktree, base, True, MESSAGE)

    assert result["ok"] is False
    assert result["commit"] is None
    assert isinstance(result["error"], str)
    assert HOOK_REASON in result["error"]
    assert _head(worktree) == base


ROLE_AUTHORS = ("cascade-specifier", "cascade-reviewer", "cascade-architect")


@pytest.mark.parametrize("author", ROLE_AUTHORS)
def test_the_commit_it_creates_is_authored_by_the_name_given(tmp_path, author):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    _leave_uncommitted_work(worktree)

    result = settle_worktree(worktree, base, True, MESSAGE, author=author)

    assert result["ok"] is True
    assert _git(worktree, "log", "-1", "--pretty=%an").stdout.strip() == author
    assert _git(worktree, "log", "-1", "--pretty=%ae").stdout.strip() == "cascade@localhost"
    assert _git(worktree, "log", "-1", "--pretty=%s").stdout.strip() == MESSAGE


def test_a_commit_without_an_author_is_authored_cascade(tmp_path):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    _leave_uncommitted_work(worktree)

    result = settle_worktree(worktree, base, True, MESSAGE)

    assert result["ok"] is True
    assert _git(worktree, "log", "-1", "--pretty=%an").stdout.strip() == "cascade"
    assert _git(worktree, "log", "-1", "--pretty=%ae").stdout.strip() == "cascade@localhost"


@pytest.mark.property
@settings(
    max_examples=24,
    deadline=None,
    suppress_health_check=[HealthCheck.function_scoped_fixture],
)
@given(verify_ok=st.booleans(), scenario=st.sampled_from(SCENARIOS), message=MESSAGES)
def test_every_settled_run_ends_in_a_resolving_commit_or_a_reason(tmp_path, verify_ok, scenario, message):
    worktree = _repository(tmp_path)
    base = _head(worktree)
    if scenario == UNCOMMITTED:
        _leave_uncommitted_work(worktree)
    elif scenario == SELF_COMMITTED:
        _commit_itself(worktree)

    result = settle_worktree(worktree, base, verify_ok, message)

    if result["ok"]:
        commit = result["commit"]
        assert isinstance(commit, str)
        assert len(commit) == 40
        resolved = _git(worktree, "rev-parse", "--verify", "--quiet", f"{commit}^{{commit}}")
        assert resolved.returncode == 0
        assert resolved.stdout.strip() == commit
        assert _head(worktree) == commit
        assert result["error"] is None
    else:
        assert result["commit"] is None
        assert isinstance(result["error"], str)
        assert result["error"].strip()
