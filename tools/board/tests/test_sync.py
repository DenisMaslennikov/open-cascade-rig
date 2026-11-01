"""Rules of the board sync, tested without a board and without GitHub.

The sync is deterministic on purpose: every decision is a pure function of (cards, columns, PRs,
policy). These tests pin those functions, so a change in the rules is a visible change here.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sync

POLICY = {
    "backlog_column": "Backlog",
    "plan_column": "Plan",
    "review_column": "Review",
    "done_column": "Done",
    "max_plan": 3,
}


def card(
    cid: int,
    title: str = "",
    done: bool = False,
    priority: int = 0,
    blocked: list[int] | None = None,
) -> dict:
    return {
        "id": cid,
        "title": title or f"card {cid}",
        "done": done,
        "priority": priority,
        "related_tasks": {"blocked": [{"id": b} for b in blocked or []]},
    }


def pr(number: int, title: str, state: str = "open", merged: bool = False) -> dict:
    return {
        "number": number,
        "title": title,
        "state": state,
        "merged_at": "2026-01-01" if merged else None,
    }


def apply(cards: dict[int, dict], columns: dict[int, str], actions: list[dict]):
    """Project the actions onto the state, the way the board would."""
    done = {cid: bool(c["done"]) for cid, c in cards.items()}
    column = dict(columns)
    for action in actions:
        if action.get("set_done"):
            done[action["card"]] = True
        column[action["card"]] = action["to"]
    return done, column


class CheckTitle(unittest.TestCase):
    def test_accepts_the_convention(self):
        for title in (
            "[#18] s0-t4: implant bonuses",
            "[#7] fix",
            "[#163] TECH DEBT — coverage",
        ):
            self.assertEqual(sync.check_title(title), [], title)

    def test_rejects_pr_number_only(self):
        self.assertTrue(sync.check_title("Skill training duration (#17) (#8)"))

    def test_rejects_reference_not_at_start(self):
        self.assertTrue(sync.check_title("s0-t4: implant bonuses [#18]"))

    def test_rejects_several_cards(self):
        problems = sync.check_title("[#18] s0-t4: implant bonuses, follow-up [#19]")
        self.assertTrue(any("exactly one card" in p for p in problems))

    def test_rejects_reference_without_text(self):
        self.assertTrue(sync.check_title("[#18]"))


class Link(unittest.TestCase):
    def test_newest_pull_request_wins(self):
        cards = {18: card(18)}
        linked, _ = sync.link_prs(
            [pr(9, "[#18] a", "closed", merged=True), pr(11, "[#18] b")], cards
        )
        self.assertEqual(linked[18]["number"], 11)

    def test_unknown_card_is_reported(self):
        linked, warnings = sync.link_prs([pr(9, "[#99] a")], {18: card(18)})
        self.assertEqual(linked, {})
        self.assertTrue(any("#99" in w for w in warnings))

    def test_title_without_card_links_nothing(self):
        linked, warnings = sync.link_prs([pr(9, "no card here")], {18: card(18)})
        self.assertEqual((linked, warnings), ({}, []))

    def test_two_open_pull_requests_for_one_card_warn(self):
        _, warnings = sync.link_prs(
            [pr(9, "[#18] a"), pr(10, "[#18] b")], {18: card(18)}
        )
        self.assertTrue(any("several open PRs" in w for w in warnings))


class Rules(unittest.TestCase):
    def test_open_pull_request_moves_the_card_to_review(self):
        cards = {18: card(18)}
        actions, _ = sync.desired_state(cards, {18: "Plan"}, [pr(9, "[#18] a")], POLICY)
        self.assertEqual(
            [(a["card"], a["from"], a["to"], a["reason"]) for a in actions],
            [(18, "Plan", "Review", "pr-open")],
        )

    def test_merged_pull_request_finishes_the_card(self):
        cards = {18: card(18)}
        actions, _ = sync.desired_state(
            cards, {18: "Review"}, [pr(9, "[#18] a", "closed", merged=True)], POLICY
        )
        self.assertEqual(len(actions), 1)
        self.assertTrue(actions[0]["set_done"])
        self.assertEqual(actions[0]["to"], "Done")

    def test_closed_unmerged_pull_request_returns_the_card_to_plan(self):
        cards = {18: card(18)}
        actions, _ = sync.desired_state(
            cards, {18: "Review"}, [pr(9, "[#18] a", "closed")], POLICY
        )
        self.assertEqual([a["to"] for a in actions], ["Plan"])

    def test_done_card_out_of_done_is_put_back(self):
        cards = {18: card(18, done=True)}
        actions, _ = sync.desired_state(cards, {18: "Review"}, [], POLICY)
        self.assertEqual(
            [(a["from"], a["to"], a["reason"]) for a in actions],
            [("Review", "Done", "done-drift")],
        )

    def test_unblocked_card_leaves_backlog(self):
        cards = {1: card(1, done=True), 2: card(2, blocked=[1])}
        actions, _ = sync.desired_state(cards, {1: "Done", 2: "Backlog"}, [], POLICY)
        self.assertEqual(
            [(a["card"], a["to"], a["reason"]) for a in actions],
            [(2, "Plan", "unblocked")],
        )

    def test_blocked_card_stays_in_backlog(self):
        cards = {1: card(1), 2: card(2, blocked=[1])}
        actions, _ = sync.desired_state(cards, {1: "Backlog", 2: "Backlog"}, [], POLICY)
        self.assertEqual([a for a in actions if a["card"] == 2], [])

    def test_missing_blocker_blocks_and_warns(self):
        cards = {2: card(2, blocked=[404])}
        actions, warnings = sync.desired_state(cards, {2: "Backlog"}, [], POLICY)
        self.assertEqual(actions, [])
        self.assertTrue(any("404" in w for w in warnings))

    def test_priority_then_id_decides_the_order(self):
        cards = {5: card(5), 3: card(3, priority=4), 1: card(1)}
        actions, _ = sync.desired_state(
            cards, {5: "Backlog", 3: "Backlog", 1: "Backlog"}, [], POLICY
        )
        self.assertEqual([a["card"] for a in actions], [3, 1, 5])

    def test_max_plan_caps_the_queue(self):
        cards = {cid: card(cid) for cid in range(1, 6)}
        columns = {cid: "Backlog" for cid in cards}
        columns[9] = "Plan"
        cards[9] = card(9)
        actions, _ = sync.desired_state(cards, columns, [], {**POLICY, "max_plan": 3})
        self.assertEqual([a["card"] for a in actions], [1, 2])

    def test_a_full_plan_moves_nothing(self):
        cards = {1: card(1), 2: card(2), 3: card(3), 4: card(4)}
        columns = {1: "Plan", 2: "Plan", 3: "Plan", 4: "Backlog"}
        actions, _ = sync.desired_state(cards, columns, [], POLICY)
        self.assertEqual(actions, [])

    def test_merge_frees_a_plan_slot_in_the_same_run(self):
        cards = {1: card(1), 2: card(2)}
        columns = {1: "Review", 2: "Review", 3: "Plan", 4: "Plan", 5: "Plan"}
        for cid in (3, 4, 5):
            cards[cid] = card(cid)
        actions, _ = sync.desired_state(
            cards, columns, [pr(9, "[#2] b", "closed", merged=True)], POLICY
        )
        self.assertIn((2, "Done"), [(a["card"], a["to"]) for a in actions])

    def test_nothing_to_do_means_no_actions(self):
        cards = {1: card(1, done=True), 2: card(2)}
        columns = {1: "Done", 2: "Review"}
        actions, warnings = sync.desired_state(
            cards, columns, [pr(9, "[#2] b")], POLICY
        )
        self.assertEqual((actions, warnings), ([], []))

    def test_apply_is_idempotent(self):
        cards = {
            1: card(1, done=True),
            2: card(2, blocked=[1]),
            3: card(3, priority=4),
            4: card(4, blocked=[3]),
        }
        columns = {1: "Review", 2: "Backlog", 3: "Backlog", 4: "Backlog"}
        prs = [pr(10, "[#4] d")]
        actions, _ = sync.desired_state(cards, columns, prs, POLICY)
        done, column = apply(cards, columns, actions)
        patched = {cid: {**c, "done": done[cid]} for cid, c in cards.items()}
        again, _ = sync.desired_state(patched, column, prs, POLICY)
        self.assertEqual(again, [])

    def test_card_without_a_bucket_counts_as_backlog(self):
        cards = {1: card(1)}
        actions, _ = sync.desired_state(cards, {}, [], POLICY)
        self.assertEqual([(a["from"], a["to"]) for a in actions], [("Backlog", "Plan")])


if __name__ == "__main__":
    unittest.main()
