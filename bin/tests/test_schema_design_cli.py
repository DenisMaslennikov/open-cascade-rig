"""The design run hands the customer's request to the generator before it calls a model.

Card #182: the `else` branch of `--reemit` passed a `brief` name that nothing on that path bound,
so a plain `--project ... --request ...` run raised `NameError` before the first gateway call. These
tests pin the value the branch must pass — the request text itself, inline or read from the `@path`
form — with the project config and the generator patched out, so the only thing that can fail is the
branch's own binding.
"""

from __future__ import annotations

import sys

import pytest

from src import schema_design

# What a successful `generate` returns; empty on purpose, the branch under test is the call itself.
EMPTY_SCHEMA = {
    "aggregates": [],
    "conventions": [],
    "enums": [],
    "migration_order": [],
    "product_summary": "",
}


@pytest.fixture
def generated(monkeypatch) -> list[dict]:
    """Record every `generate` call and keep the run off the gateway, the ledger and the state dir."""
    calls: list[dict] = []

    def fake_generate(cfg, brief, requirements, repo, out_dir, workers):
        calls.append({"cfg": cfg, "brief": brief, "requirements": requirements, "out_dir": out_dir, "workers": workers})
        return dict(EMPTY_SCHEMA), [], []

    monkeypatch.setattr(schema_design, "project_config", lambda slug: {"model": "architect-test", "_key": "unused"})
    monkeypatch.setattr(schema_design, "generate", fake_generate)
    return calls


def run_cli(monkeypatch, out_dir: Path, *argv: str) -> int:
    monkeypatch.setattr(sys, "argv", ["schema_design.py", "--project", "cascade-rig", "--out-dir", str(out_dir), *argv])
    return schema_design.main()


def test_the_request_text_reaches_the_generator(monkeypatch, generated, tmp_path):
    request = "Accounts die with the user; characters belong to an account; ESI bodies are cache."

    assert run_cli(monkeypatch, tmp_path, "--request", request) == 0

    assert [call["brief"] for call in generated] == [request]


def test_a_request_given_as_a_path_is_read_from_that_file(monkeypatch, generated, tmp_path):
    brief_path = tmp_path / "contract-request.md"
    brief_path.write_text("One aggregate per dbhelpers module; money is numeric(20,2).\n", encoding="utf-8")

    assert run_cli(monkeypatch, tmp_path / "out", "--request", f"@{brief_path}") == 0

    assert [call["brief"] for call in generated] == [brief_path.read_text(encoding="utf-8")]


def test_the_design_run_writes_the_contract_it_generated(monkeypatch, generated, tmp_path):
    """The branch must not only reach `generate`; its payload is what the artifacts are written from."""

    assert run_cli(monkeypatch, tmp_path, "--request", "A small schema.") == 0

    payload = (tmp_path / "schema.json").read_text(encoding="utf-8")
    assert '"project": "cascade-rig"' in payload
    assert (tmp_path / "schema.dbml").exists() and (tmp_path / "schema.md").exists()
