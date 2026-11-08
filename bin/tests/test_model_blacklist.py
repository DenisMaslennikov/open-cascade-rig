"""A configured model can be forbidden, not only missing.

`check_project` already refuses a project whose role has no model. The owner also wants the pipeline to
refuse a model it must never use, so this module freezes the pure decision behind that check:
`blacklist_problems(models, entries)` turns a role → model mapping and the parsed blacklist into one
`(role, model, reason)` tuple per hit.

The expected values come from the rules of the card, not from an implementation: an exact name, a `*`
prefix, case-insensitive matching over the whole name, role-name order, the matching entry's own reason,
and an empty model treated as the existing "unset" failure rather than a blacklist hit. The last two
tests pin the canonical `config/models-blacklist.yaml` — the file must exist and must carry
`qwen/qwen3-coder-next` with a non-empty reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from rig.check_project import blacklist_problems

MODEL = "qwen/qwen3-coder-next"
REASON = "its runs were abandoned twice on a large diff"
BLACKLIST_FILE = Path("config") / "models-blacklist.yaml"

BLACKLIST_CASES = [
    pytest.param(
        {"executor": MODEL},
        [{"model": MODEL, "reason": REASON}],
        [("executor", MODEL, REASON)],
        id="an-exact-model-name-is-reported-with-its-own-reason",
    ),
    pytest.param(
        {"executor": MODEL},
        [{"model": "qwen/qwen3-coder-*", "reason": "the family is retired"}],
        [("executor", MODEL, "the family is retired")],
        id="a-star-entry-catches-every-model-under-its-prefix",
    ),
    pytest.param(
        {"executor": "qwen/qwen3-coder"},
        [{"model": MODEL, "reason": REASON}],
        [],
        id="a-shorter-model-does-not-equal-an-exact-entry",
    ),
    pytest.param(
        {"executor": "qwen/qwen3-coder"},
        [{"model": f"{MODEL}*", "reason": REASON}],
        [],
        id="a-model-shorter-than-a-star-prefix-is-not-a-hit",
    ),
    pytest.param(
        {"executor": f"{MODEL}-plus"},
        [{"model": MODEL, "reason": REASON}],
        [],
        id="an-exact-entry-does-not-reach-a-longer-model",
    ),
    pytest.param(
        {"executor": MODEL, "architect": MODEL, "reviewer": "~z-ai/glm-flash-latest"},
        [{"model": MODEL, "reason": REASON}],
        [("architect", MODEL, REASON), ("executor", MODEL, REASON)],
        id="two-roles-are-reported-in-role-name-order",
    ),
    pytest.param(
        {"executor": "QWEN/Qwen3-Coder-Next"},
        [{"model": MODEL, "reason": REASON}],
        [("executor", "QWEN/Qwen3-Coder-Next", REASON)],
        id="matching-is-case-insensitive-and-reports-the-configured-spelling",
    ),
    pytest.param(
        {"executor": "Qwen/Qwen3-Coder-Flash"},
        [{"model": "QWEN/QWEN3-CODER-*", "reason": REASON}],
        [("executor", "Qwen/Qwen3-Coder-Flash", REASON)],
        id="a-star-prefix-matches-case-insensitively",
    ),
    pytest.param(
        {"executor": MODEL},
        [{"model": MODEL, "reason": ""}],
        [("executor", MODEL, "")],
        id="a-blank-reason-still-reports-the-hit",
    ),
    pytest.param(
        {"executor": MODEL, "reviewer": "openai/gpt-5-mini"},
        [
            {"model": "openai/gpt-5-nano", "reason": "not this one"},
            {"model": "qwen/qwen3-coder-*", "reason": "the qwen coder family"},
        ],
        [("executor", MODEL, "the qwen coder family")],
        id="the-reason-comes-from-the-entry-that-matched",
    ),
    pytest.param({}, [{"model": MODEL, "reason": REASON}], [], id="no-models-report-nothing"),
    pytest.param({"executor": MODEL}, [], [], id="no-entries-report-nothing"),
    pytest.param(
        {"executor": "", "reviewer": "~z-ai/glm-flash-latest"},
        [{"model": "*", "reason": "every model is forbidden"}],
        [("reviewer", "~z-ai/glm-flash-latest", "every model is forbidden")],
        id="an-empty-model-is-an-unset-model-not-a-blacklisted-one",
    ),
]


@pytest.mark.parametrize(("models", "entries", "expected"), BLACKLIST_CASES)
def test_blacklist_problems(
    models: dict[str, str],
    entries: list[dict[str, str]],
    expected: list[tuple[str, str, str]],
) -> None:
    """Each configured model the blacklist covers yields one tuple, in role-name order."""
    assert blacklist_problems(models=models, entries=entries) == expected


def _read_entries(path: Path) -> list[dict[str, str]]:
    """Reads the `- model:` blocks of the flat blacklist file into one mapping per entry."""
    entries: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line == "models:":
            continue
        if line.startswith("- "):
            current = {}
            entries.append(current)
            line = line[2:].strip()
        if current is None:
            continue
        key, separator, value = line.partition(":")
        if separator:
            current[key.strip()] = value.strip().strip('"').strip("'")
    return entries


def test_the_canonical_blacklist_file_lives_in_the_config_directory(repo_root: Path) -> None:
    """The list is versioned canon next to the other configuration, not per-machine state."""
    assert (repo_root / BLACKLIST_FILE).is_file()


def test_the_canonical_blacklist_carries_qwen3_coder_next_with_a_reason(repo_root: Path) -> None:
    """The first entry the owner named is listed, and the entry says why the model is forbidden."""
    path = repo_root / BLACKLIST_FILE
    assert path.is_file(), f"{BLACKLIST_FILE} must exist in the canonical config"
    hits = [entry for entry in _read_entries(path) if entry.get("model") == MODEL]
    assert hits, f"{MODEL} is not listed in {BLACKLIST_FILE}"
    assert any(entry.get("reason", "").strip() for entry in hits), f"the {MODEL} entry has no reason"
