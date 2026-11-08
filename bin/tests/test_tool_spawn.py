"""A rig tool starts its sibling as a package module, never as a file path.

Card #191 moved the scripts into `bin/rig/`, but every module that spawns another one still passes
`str(BIN / "<tool>.py")` to `python`. `usage.py` opens with a relative import, so Python answers
`ImportError: attempted relative import with no known parent package`; the caller captures and drops
that output, and no ledger entry is written at all.

Two contracts are frozen here. First the behaviour: a recorded role run leaves exactly one line in
the project ledger, and a ledger that cannot be created is reported on stderr instead of vanishing.
Second the guard: a static scan of every package module rejects a `sys.executable` command whose
argument is a `<directory> / "<module>.py"` path, because the only working invocation is
`-m rig.<tool>`.
"""

from __future__ import annotations

import ast
import json
import pkgutil
from pathlib import Path

import pytest
import rig
from rig import run_role, usage

RIG_DIR = Path(rig.__file__).resolve().parent
MODULES = sorted(info.name for info in pkgutil.iter_modules(rig.__path__) if not info.name.startswith("_"))

SLUG = "cascade-rig"
ROLE = "reviewer"
MODEL = "vendor/model-a"
USAGE = {
    "prompt_tokens": 1200,
    "completion_tokens": 345,
    "cache_read_tokens": 67,
    "cost_usd": 0.5,
}


def _write_pricing_cache(config_root: Path) -> None:
    """A local price cache, so the child never asks the gateway for a price."""
    path = config_root / "cascade" / "pricing.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({MODEL: {"in": 1e-6, "out": 2e-6}}), encoding="utf-8")


@pytest.fixture
def ledger_roots(monkeypatch, tmp_path: Path) -> Path:
    """Point both XDG roots at temporary directories and give the project its ledger directory."""
    state_root = tmp_path / "state"
    config_root = tmp_path / "config"
    (state_root / "cascade" / SLUG).mkdir(parents=True)
    _write_pricing_cache(config_root)
    monkeypatch.setenv("XDG_STATE_HOME", str(state_root))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    monkeypatch.setattr(usage, "STATE", state_root / "cascade")
    return state_root


def test_a_recorded_role_run_appends_one_line_to_the_project_ledger(ledger_roots: Path):
    ledger = usage.ledger_path(SLUG)
    assert not ledger.exists()

    run_role.record_usage(SLUG, ROLE, MODEL, USAGE)

    assert ledger.exists()
    lines = [line for line in ledger.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert len(lines) == 1

    event = json.loads(lines[0])
    assert event["project"] == SLUG
    assert event["role"] == ROLE
    assert event["model"] == MODEL
    assert event["input_tokens"] == USAGE["prompt_tokens"]
    assert event["output_tokens"] == USAGE["completion_tokens"]
    assert event["feature"] == f"_role:{ROLE}"


def test_a_ledger_that_cannot_be_created_is_reported_on_stderr(monkeypatch, tmp_path: Path, capsys):
    broken_state = tmp_path / "state-is-a-file"
    broken_state.write_text("not a directory", encoding="utf-8")
    config_root = tmp_path / "config"
    _write_pricing_cache(config_root)
    monkeypatch.setenv("XDG_STATE_HOME", str(broken_state))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_root))
    ledger = broken_state / "cascade" / SLUG / "usage.jsonl"

    run_role.record_usage(SLUG, ROLE, MODEL, USAGE)

    message = capsys.readouterr().err
    assert message.strip(), "record_usage swallowed the failed ledger write"
    assert any(marker in message for marker in (str(ledger), "usage.jsonl", SLUG, "Not a directory")), (
        f"the failure does not name the ledger or the error: {message!r}"
    )


def _is_sys_executable(node: ast.expr) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "executable"
        and isinstance(node.value, ast.Name)
        and node.value.id == "sys"
    )


def _launched_module_path(node: ast.expr) -> str | None:
    """The source of a `<directory> / "<module>.py"` argument, also when wrapped in `str(...)`."""
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "str" and len(node.args) == 1:
        node = node.args[0]
    if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
        return None
    right = node.right
    if not isinstance(right, ast.Constant) or not isinstance(right.value, str):
        return None
    name = right.value.rsplit("/", 1)[-1]
    if not name.endswith(".py") or name[:-3] not in MODULES:
        return None
    return ast.unparse(node)


def file_path_launches(source: str) -> list[tuple[int, str]]:
    """Every `sys.executable` command whose argument is a path to a package module."""
    violations: list[tuple[int, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, (ast.List, ast.Tuple)) and node.elts and _is_sys_executable(node.elts[0]):
            elements = node.elts[1:]
        elif isinstance(node, ast.Call) and node.args and _is_sys_executable(node.args[0]):
            elements = node.args[1:]
        else:
            continue
        for element in elements:
            expression = _launched_module_path(element)
            if expression is not None:
                violations.append((node.lineno, expression))
    return violations


def test_the_package_offers_modules_to_scan():
    """An empty module list would make the guard pass without checking anything."""
    assert {"run_role", "usage", "gate", "run_subtask"} <= set(MODULES)


@pytest.mark.parametrize("module", MODULES)
def test_no_module_launches_a_sibling_tool_by_file_path(module: str):
    source = (RIG_DIR / f"{module}.py").read_text(encoding="utf-8")

    violations = file_path_launches(source)

    assert not violations, "\n".join(
        f"{module}.py:{line}: a sibling tool is launched by path: {expression}" for line, expression in violations
    )


@pytest.mark.parametrize(
    ("source", "expression"),
    [
        pytest.param('cmd = [sys.executable, str(BIN / "usage.py")]\n', "BIN / 'usage.py'", id="str-wrapped"),
        pytest.param('subprocess.run([sys.executable, BIN / "gate.py"])\n', "BIN / 'gate.py'", id="bare"),
    ],
)
def test_the_guard_detects_a_module_launched_by_path(source: str, expression: str):
    assert file_path_launches(source) == [(1, expression)]


@pytest.mark.parametrize(
    "source",
    [
        pytest.param('subprocess.run([sys.executable, "-m", "rig.usage", "report"])\n', id="module-mode"),
        pytest.param('cmd = [sys.executable, "-m", "rig.gate", "expect-fail"]\n', id="module-mode-assigned"),
        pytest.param("subprocess.run([sys.executable, str(tmp_path)])\n", id="plain-path-argument"),
        pytest.param("python = sys.executable if missing else str(RENDER_PY)\n", id="runtime-fallback"),
        pytest.param(
            'subprocess.run([sys.executable, str(BIN.parent / "docs/design/render.py")])\n',
            id="design-asset",
        ),
    ],
)
def test_the_guard_ignores_launches_that_are_not_module_paths(source: str):
    assert file_path_launches(source) == []
