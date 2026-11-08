"""The `test_review` step asks whether frozen tests can be satisfied before a run is spent.

The step itself is one non-agentic model call. Only its three pure functions are pinned here: `build_user`
assembles the message from a `plan.json` artifact, `parse_verdict` reads and normalises the answer, and
`decide` names who acts next. No model, no network, no clock and no filesystem: every expected value comes
from the rules of the card.

The plan artifact is the one `plan_task.py` writes: top-level `assumptions`, and the subtasks under the
`plan` key. A subtask's `goal` is the logic description of the reviewed function.
"""

from __future__ import annotations

import json

import pytest
from rig.test_review import build_user, decide, parse_verdict

SUBTASK_ID = "test-review-tests"
OTHER_SUBTASK_ID = "unrelated-tests"

SIGNATURES = [
    "build_user(plan: dict, subtask_id: str, stub_text: str, test_text: str, gate_output: str) -> str",
    "parse_verdict(text: str) -> dict",
    "decide(verdict: dict) -> str",
]
GOAL = "The step checks each frozen function's tests against the plan's logic description and acceptance pairs."
OTHER_GOAL = "An unrelated subtask that the selected subtask must not be confused with."
ASSUMPTIONS = [
    "The step is a single non-agentic call, not an agent in the worktree.",
    "The model is a vendor different from the specifier's and the executor's.",
]
ACCEPTANCE = [
    {"in": "a verdict that says pass while carrying a blocker finding", "out": "the parsed verdict is fail"},
    {"in": "a fail verdict with a plan_inconsistent finding", "out": "decide returns architect"},
]

STUB_TEXT = (
    '"""Review one frozen function before the executor spends a run on it."""\n'
    "\n"
    "def build_user(plan: dict, subtask_id: str, stub_text: str, test_text: str, gate_output: str) -> str:\n"
    '    """Assemble the message the reviewer receives."""\n'
    "    raise NotImplementedError\n"
)
TEST_TEXT = (
    "def test_a_clean_pass_goes_to_the_executor():\n"
    '    assert decide({"verdict": "pass", "findings": []}) == "executor"\n'
)
GATE_OUTPUT = (
    "FAILED tests/test_test_review.py::test_a_clean_pass_goes_to_the_executor "
    "- NotImplementedError\n1 failed in 0.03s\n"
)

FINDING_KEYS = ("kind", "severity", "test", "witness", "reason")


def _finding(
    kind: str = "unsatisfiable",
    severity: str = "blocker",
    test: str = "tests/test_mod.py::test_case",
    witness: str = "an input -> the output the description requires",
    reason: str = "why the test cannot be satisfied",
) -> dict:
    return {"kind": kind, "severity": severity, "test": test, "witness": witness, "reason": reason}


def _answer(verdict: str, findings: list[dict] | None = None) -> dict:
    return {"verdict": verdict, "findings": [] if findings is None else findings}


def _plan() -> dict:
    """A plan artifact shaped like the one `plan_task.py` writes."""
    return {
        "status": "ready",
        "questions": [],
        "assumptions": list(ASSUMPTIONS),
        "plan": {
            "task": {
                "id": "test-review-contract",
                "title": "Freeze the test_review contract",
                "goal": GOAL,
                "acceptance_summary": "The three pure functions of the step are frozen and red.",
            },
            "subtasks": [
                {
                    "id": SUBTASK_ID,
                    "kind": "tests",
                    "title": "Freeze the test_review contract",
                    "goal": GOAL,
                    "files_allowed": ["bin/tests/test_test_review.py"],
                    "contract": {"new_signatures": list(SIGNATURES), "breaks": []},
                    "acceptance": [dict(pair) for pair in ACCEPTANCE],
                    "depends_on": [],
                    "test_command": "cd bin && ../.venv/bin/python -m pytest tests/test_test_review.py -q",
                },
                {
                    "id": OTHER_SUBTASK_ID,
                    "kind": "tests",
                    "title": "An unrelated frozen suite",
                    "goal": OTHER_GOAL,
                    "files_allowed": ["bin/tests/test_something_else.py"],
                    "contract": {"new_signatures": ["something_else() -> None"], "breaks": []},
                    "acceptance": [{"in": "nothing", "out": "nothing"}],
                    "depends_on": [],
                    "test_command": "cd bin && ../.venv/bin/python -m pytest tests/test_something_else.py -q",
                },
            ],
        },
    }


def _empty_headings(message: str) -> list[str]:
    """Lines that look like a heading but are followed by nothing: `Heading:` then a blank line or EOF."""
    lines = message.splitlines()
    empty = []
    for index, line in enumerate(lines):
        stripped = line.strip()
        following = lines[index + 1].strip() if index + 1 < len(lines) else ""
        if stripped.endswith(":") and not following:
            empty.append(stripped)
    return empty


def test_build_user_carries_the_selected_subtask_the_stub_the_tests_and_the_gate_output():
    message = build_user(_plan(), SUBTASK_ID, STUB_TEXT, TEST_TEXT, GATE_OUTPUT)

    for piece in (*SIGNATURES, GOAL, *ASSUMPTIONS, STUB_TEXT, TEST_TEXT, GATE_OUTPUT):
        assert piece in message
    for pair in ACCEPTANCE:
        assert pair["in"] in message
        assert pair["out"] in message


def test_build_user_is_deterministic_for_the_same_plan():
    plan = _plan()

    assert build_user(plan, SUBTASK_ID, STUB_TEXT, TEST_TEXT, GATE_OUTPUT) == build_user(
        plan, SUBTASK_ID, STUB_TEXT, TEST_TEXT, GATE_OUTPUT
    )


def test_build_user_without_assumptions_leaves_no_empty_heading():
    plan = _plan()
    del plan["assumptions"]

    message = build_user(plan, SUBTASK_ID, STUB_TEXT, TEST_TEXT, GATE_OUTPUT)

    assert _empty_headings(message) == []
    assert "None" not in message
    assert GOAL in message
    assert STUB_TEXT in message
    assert TEST_TEXT in message
    assert GATE_OUTPUT in message


def test_build_user_without_acceptance_leaves_no_empty_heading():
    plan = _plan()
    del plan["plan"]["subtasks"][0]["acceptance"]

    message = build_user(plan, SUBTASK_ID, STUB_TEXT, TEST_TEXT, GATE_OUTPUT)

    assert _empty_headings(message) == []
    assert "None" not in message
    assert GOAL in message
    assert STUB_TEXT in message
    assert TEST_TEXT in message
    assert GATE_OUTPUT in message


def test_an_unknown_subtask_mentions_the_requested_id_and_the_ids_the_plan_carries():
    with pytest.raises(SystemExit) as error:
        build_user(_plan(), "no-such-subtask", STUB_TEXT, TEST_TEXT, GATE_OUTPUT)

    message = str(error.value)
    assert "no-such-subtask" in message
    assert SUBTASK_ID in message
    assert OTHER_SUBTASK_ID in message


NORMALISATION_CASES = [
    pytest.param("pass", [], "pass", id="a-clean-pass"),
    pytest.param("pass", [_finding(severity="minor")], "pass", id="a-pass-with-a-minor-finding"),
    pytest.param("fail", [_finding(severity="blocker")], "fail", id="a-fail-on-a-blocker"),
    pytest.param("fail", [_finding(severity="major")], "fail", id="a-fail-on-a-major"),
    pytest.param("fail", [_finding(severity="minor"), _finding(severity="minor")], "pass", id="only-minor-is-a-pass"),
    pytest.param("fail", [], "pass", id="no-findings-is-a-pass"),
    pytest.param("pass", [_finding(severity="blocker")], "fail", id="a-pass-contradicted-by-a-blocker-is-a-fail"),
    pytest.param(
        "pass",
        [_finding(severity="major"), _finding(severity="minor")],
        "fail",
        id="a-pass-contradicted-by-a-major-is-a-fail",
    ),
    pytest.param(
        "fail",
        [_finding(severity="minor"), _finding(severity="major")],
        "fail",
        id="one-major-overrides-minor-findings",
    ),
]


@pytest.mark.parametrize(("declared", "findings", "expected"), NORMALISATION_CASES)
def test_parse_verdict_normalises_the_verdict_from_the_findings(declared, findings, expected):
    parsed = parse_verdict(json.dumps(_answer(declared, findings)))

    assert set(parsed) == {"verdict", "findings"}
    assert parsed["verdict"] == expected
    assert len(parsed["findings"]) == len(findings)


def test_a_parsed_finding_keeps_the_five_documented_fields():
    finding = _finding(
        kind="contradicts_spec",
        severity="major",
        test="tests/test_mod.py::test_the_rule",
        witness="3 -> 9",
        reason="the description says 3 -> 9, the test asserts 3 -> 8",
    )

    parsed = parse_verdict(json.dumps(_answer("fail", [finding])))

    for key in FINDING_KEYS:
        assert parsed["findings"][0][key] == finding[key]


TEXT_RENDERERS = [
    pytest.param(lambda text: text, id="bare-json"),
    pytest.param(lambda text: f"```json\n{text}\n```", id="fenced-json"),
    pytest.param(lambda text: f"Here is my review.\n\n{text}\n\nThat is all I found.", id="json-surrounded-by-prose"),
]


@pytest.mark.parametrize("render", TEXT_RENDERERS)
def test_parse_verdict_reads_json_wherever_it_sits(render):
    answer = _answer("fail", [_finding(severity="major", test="tests/test_mod.py::test_the_rule")])

    parsed = parse_verdict(render(json.dumps(answer)))

    assert parsed["verdict"] == "fail"
    assert parsed["findings"][0]["test"] == "tests/test_mod.py::test_the_rule"


VALUE_ERROR_CASES = [
    pytest.param("There is no JSON in this answer.", "There is no JSON", id="no-json-at-all"),
    pytest.param(
        json.dumps({"verdict": "maybe", "findings": []}),
        "maybe",
        id="a-verdict-other-than-pass-or-fail",
    ),
    pytest.param(
        json.dumps(
            {
                "verdict": "fail",
                "findings": [
                    {
                        "kind": "unsatisfiable",
                        "severity": "major",
                        "test": "tests/test_mod.py::test_missing_reason",
                        "witness": "an input -> an output",
                    }
                ],
            }
        ),
        "tests/test_mod.py::test_missing_reason",
        id="a-finding-missing-a-required-key",
    ),
    pytest.param(
        json.dumps(_answer("fail", [_finding(severity="critical")])),
        "critical",
        id="a-severity-outside-blocker-major-minor",
    ),
    pytest.param(
        json.dumps(_answer("fail", [_finding(kind="invented_kind")])),
        "invented_kind",
        id="a-kind-outside-the-vocabulary",
    ),
]


@pytest.mark.parametrize(("text", "offending"), VALUE_ERROR_CASES)
def test_parse_verdict_rejects_a_malformed_answer_with_the_offending_text(text, offending):
    with pytest.raises(ValueError) as error:
        parse_verdict(text)

    assert offending in str(error.value)


DECISION_CASES = [
    pytest.param(_answer("pass"), "executor", id="a-clean-pass-goes-to-the-executor"),
    pytest.param(
        _answer("pass", [_finding(severity="minor")]),
        "executor",
        id="a-minor-pass-goes-to-the-executor",
    ),
    pytest.param(
        _answer("pass", [_finding(kind="plan_inconsistent", severity="minor")]),
        "executor",
        id="a-minor-plan-finding-does-not-change-a-pass",
    ),
    pytest.param(
        _answer("fail", [_finding(kind="unsatisfiable", severity="blocker")]),
        "specifier",
        id="an-unsatisfiable-blocker-goes-to-the-specifier",
    ),
    pytest.param(
        _answer("fail", [_finding(kind="contradicts_spec", severity="major")]),
        "specifier",
        id="a-contradiction-goes-to-the-specifier",
    ),
    pytest.param(
        _answer(
            "fail",
            [
                _finding(kind="underspecified", severity="minor"),
                _finding(kind="asserts_stub", severity="major"),
            ],
        ),
        "specifier",
        id="an-asserts-stub-finding-goes-to-the-specifier",
    ),
    pytest.param(
        _answer("fail", [_finding(kind="plan_inconsistent", severity="blocker")]),
        "architect",
        id="a-plan-inconsistency-goes-to-the-architect",
    ),
    pytest.param(
        _answer(
            "fail",
            [
                _finding(kind="contradicts_spec", severity="major"),
                _finding(kind="plan_inconsistent", severity="minor"),
            ],
        ),
        "architect",
        id="a-plan-finding-anywhere-in-the-list-goes-to-the-architect",
    ),
]


@pytest.mark.parametrize(("verdict", "expected"), DECISION_CASES)
def test_decide_names_who_acts_next(verdict, expected):
    assert decide(verdict) == expected


def test_a_fail_that_carries_a_plan_inconsistency_goes_to_the_architect():
    parsed = parse_verdict(json.dumps(_answer("fail", [_finding(kind="plan_inconsistent", severity="major")])))

    assert parsed["verdict"] == "fail"
    assert decide(parsed) == "architect"
