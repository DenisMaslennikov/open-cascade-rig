#!/usr/bin/env python3
"""Deterministic board sync: GitHub → Vikunja. No agents, no guessing, no polling required.

The contract (one card per pull request, the card says which):

    [#18] s0-t4: implant bonuses on top of base attributes

A pull request owns a card when its **title starts** with `[#<card>]`. GitHub appends
`(#<pr>)` to a squash title, so a bare `#17` cannot tell a card from a pull request;
the bracketed prefix can, and it always sits in the same place.

The board follows GitHub, never the other way round:

    open PR      → Review
    merged PR    → Done (and `done = true`)
    closed PR    → Plan (the work went back to the queue)
    done card    → Done (repairs a card left in another column)
    all blockers done → Backlog → Plan, highest priority first, at most `max_plan`
    in Plan at a time (a card blocked by a card that is not in `cards` stays put)

The run is idempotent: every action is derived from the current state, applied, then the
state is recomputed and must produce no actions. A run with nothing to do prints nothing —
silence means "nothing changed"; that is also the no-notification rule.

CLI:
    sync.py --check-title "[#18] s0-t4: implant bonuses"   # exit 1 when the title breaks the rule
    sync.py                       # dry run, prints the actions it would take
    sync.py --apply               # do it
    sync.py --apply --json        # machine-readable report

Environment:
    VIKUNJA_URL, VIKUNJA_TOKEN    # falls back to ~/.config/vikunja/creds.json login locally
    GITHUB_REPOSITORY, GITHUB_TOKEN  # falls back to `gh pr list` locally
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

CARD_AT_START = re.compile(r"^\[#(\d+)\]\s+\S")
CARD_ANYWHERE = re.compile(r"\[#(\d+)\]")

DEFAULT_POLICY = {
    "backlog_column": "Backlog",
    "plan_column": "Plan",
    "review_column": "Review",
    "done_column": "Done",
    "max_plan": 3,
    "max_prs": 100,
}


# --------------------------------------------------------------------------- rules (pure)


def check_title(title: str) -> list[str]:
    """Everything wrong with one pull-request title. Empty list means it is fine."""
    problems: list[str] = []
    refs = CARD_ANYWHERE.findall(title or "")
    if not CARD_AT_START.match(title or ""):
        problems.append(
            "title must start with the card reference, e.g. `[#18] s0-t4: implant bonuses`"
        )
    if len(refs) != 1:
        found = ", ".join(f"#{r}" for r in refs) or "none"
        problems.append(
            f"title must name exactly one card, found {len(refs)} ({found})"
        )
    return problems


def card_of(pr: dict) -> int | None:
    match = CARD_AT_START.match(pr.get("title") or "")
    return int(match.group(1)) if match else None


def pr_state(pr: dict) -> str:
    """`merged`, `open` or `closed` — accepted from both the REST payload and `gh --json`."""
    if pr.get("merged_at") or pr.get("mergedAt"):
        return "merged"
    return "open" if str(pr.get("state", "")).lower() == "open" else "closed"


def link_prs(
    prs: list[dict], cards: dict[int, dict]
) -> tuple[dict[int, dict], list[str]]:
    """card id → the newest pull request that names it, plus everything suspicious."""
    linked: dict[int, dict] = {}
    warnings: list[str] = []
    open_by_card: dict[int, list[int]] = {}
    for pr in prs:
        cid = card_of(pr)
        if cid is None:
            continue
        if cid not in cards:
            warnings.append(
                f"PR #{pr['number']} names card #{cid}, which does not exist"
            )
            continue
        if pr_state(pr) == "open":
            open_by_card.setdefault(cid, []).append(int(pr["number"]))
        previous = linked.get(cid)
        if previous is None or int(pr["number"]) > int(previous["number"]):
            linked[cid] = pr
    for cid, numbers in open_by_card.items():
        if len(numbers) > 1:
            warnings.append(
                f"card #{cid} has several open PRs ({sorted(numbers)}), the newest wins"
            )
    return linked, warnings


def blockers_of(card: dict) -> list[int]:
    """Cards that must be done before this one.

    Vikunja mirrors a relation on both sides: `blocking` lists what *this* card blocks,
    `blocked` lists what blocks it. Only `blocked` is read here.
    """
    related = card.get("related_tasks") or {}
    out: list[int] = []
    for item in related.get("blocked") or []:
        if isinstance(item, dict) and item.get("id"):
            out.append(int(item["id"]))
    return out


def desired_state(
    cards: dict[int, dict], columns: dict[int, str], prs: list[dict], policy: dict
) -> tuple[list[dict], list[str]]:
    """Every action that takes the board from `cards`/`columns` to the state GitHub implies."""
    policy = {**DEFAULT_POLICY, **policy}
    linked, warnings = link_prs(prs, cards)
    plan_column = policy["plan_column"]
    done_column = policy["done_column"]
    backlog_column = policy["backlog_column"]

    column = {cid: (columns.get(cid) or backlog_column) for cid in cards}
    done = {cid: bool(card.get("done")) for cid, card in cards.items()}
    priority = {cid: int(card.get("priority") or 0) for cid, card in cards.items()}
    actions: list[dict] = []

    def act(cid: int, to: str, reason: str, pr: int | None = None, **extra) -> None:
        if column[cid] == to and not extra.get("set_done"):
            return  # already where the rules want it: an action here would never converge
        action = {"card": cid, "from": column[cid], "to": to, "reason": reason}
        if pr is not None:
            action["pr"] = pr
        action.update(extra)
        column[cid] = to
        actions.append(action)

    # 1. What the pull request says.
    for cid in sorted(linked):
        pr = linked[cid]
        number = int(pr["number"])
        state = pr_state(pr)
        if state == "merged" and not done[cid]:
            done[cid] = True
            act(cid, done_column, "pr-merged", number, set_done=True)
        elif state == "open" and not done[cid]:
            act(cid, policy["review_column"], "pr-open", number)
        elif (
            state == "closed"
            and not done[cid]
            and column[cid] == policy["review_column"]
        ):
            act(cid, plan_column, "pr-closed", number)

    # 2. A card flagged done belongs in Done, whatever moved it.
    for cid in sorted(cards):
        if done[cid] and column[cid] != done_column:
            act(cid, done_column, "done-drift")

    # 3. Refill Plan with unblocked Backlog cards, in order.
    free = max(
        0,
        int(policy["max_plan"])
        - sum(1 for cid in column if column[cid] == plan_column),
    )
    queue: list[int] = []
    for cid in sorted(cards):
        if done[cid] or column[cid] != backlog_column:
            continue
        blockers = blockers_of(cards[cid])
        unknown = [b for b in blockers if b not in cards]
        if unknown:
            warnings.append(f"card #{cid} is blocked by missing card(s) {unknown}")
            continue
        waiting = [b for b in blockers if not done[b]]
        if waiting:
            continue
        queue.append(cid)
    queue.sort(key=lambda cid: (-priority[cid], cid))
    for cid in queue[:free]:
        act(cid, plan_column, "unblocked", unblocked_by=blockers_of(cards[cid]))

    return actions, warnings


# --------------------------------------------------------------------------- Vikunja


class Vikunja:
    """The four calls this sync needs; nothing else from the board adapter is imported."""

    def __init__(
        self, base: str, project_id: int, view_id: int, token: str | None = None
    ):
        self.base = base.rstrip("/")
        self.project_id = project_id
        self.view_id = view_id
        self.token = token

    def _auth(self) -> str:
        if self.token:
            return self.token
        creds = Path(os.path.expanduser("~/.config/vikunja/creds.json"))
        if not creds.exists():
            raise SystemExit("no VIKUNJA_TOKEN and no ~/.config/vikunja/creds.json")
        cfg = json.loads(creds.read_text())
        _, body = self._raw(
            "POST",
            "/login",
            {"username": cfg["username"], "password": cfg["password"]},
            auth=False,
        )
        self.token = body["token"]
        return self.token

    def _raw(self, method: str, path: str, body=None, auth: bool = True):
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.base + path, data=data, method=method)
        request.add_header("Content-Type", "application/json")
        if auth:
            request.add_header("Authorization", "Bearer " + self._auth())
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                raw = response.read().decode()
                return response.status, (json.loads(raw) if raw.strip() else {})
        except urllib.error.HTTPError as error:
            raise SystemExit(
                f"{method} {path} -> {error.code}: {error.read().decode()[:300]}"
            )

    def cards(self) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for page in range(1, 41):
            _, chunk = self._raw(
                "GET", f"/projects/{self.project_id}/tasks?per_page=50&page={page}"
            )
            for card in chunk or []:
                out[int(card["id"])] = card
            if len(chunk or []) < 50:
                break
        return out

    def columns(self) -> dict[int, str]:
        """task id → column title; a card in no bucket is absent and counts as Backlog."""
        out: dict[int, str] = {}
        for page in range(1, 21):
            _, buckets = self._raw(
                "GET",
                f"/projects/{self.project_id}/views/{self.view_id}/tasks?per_page=200&page={page}",
            )
            fresh = 0
            for bucket in buckets or []:
                for card in bucket.get("tasks") or []:
                    if int(card["id"]) not in out:
                        fresh += 1
                    out[int(card["id"])] = bucket["title"]
            if not fresh:
                break
        return out

    def move(self, card_id: int, column: str) -> None:
        buckets = (
            self._raw(
                "GET", f"/projects/{self.project_id}/views/{self.view_id}/buckets"
            )[1]
            or []
        )
        by_title = {bucket["title"]: bucket["id"] for bucket in buckets}
        if column not in by_title:
            raise SystemExit(f"no column {column!r}; have {sorted(by_title)}")
        self._raw(
            "POST",
            f"/projects/{self.project_id}/views/{self.view_id}/buckets/{by_title[column]}/tasks",
            {"task_id": card_id},
        )

    def set_done(self, card_id: int) -> None:
        self._raw("POST", f"/tasks/{card_id}", {"done": True})


# --------------------------------------------------------------------------- GitHub


def fetch_prs(repo: str, token: str | None, limit: int) -> list[dict]:
    if token:
        prs: list[dict] = []
        page = 1
        while len(prs) < limit:
            url = (
                f"https://api.github.com/repos/{repo}/pulls"
                f"?state=all&sort=created&direction=desc&per_page=100&page={page}"
            )
            request = urllib.request.Request(url)
            request.add_header("Accept", "application/vnd.github+json")
            request.add_header("Authorization", "Bearer " + token)
            request.add_header("User-Agent", "board-sync")
            try:
                with urllib.request.urlopen(request, timeout=30) as response:
                    chunk = json.loads(response.read().decode() or "[]")
            except urllib.error.HTTPError as error:
                raise SystemExit(
                    f"GitHub API -> {error.code}: {error.read().decode()[:300]}"
                )
            if not chunk:
                break
            prs.extend(chunk)
            page += 1
        return prs[:limit]
    proc = subprocess.run(
        [
            "gh",
            "pr",
            "list",
            "--repo",
            repo,
            "--state",
            "all",
            "--limit",
            str(limit),
            "--json",
            "number,title,state,mergedAt",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise SystemExit(f"gh pr list failed: {proc.stderr.strip()[:300]}")
    return json.loads(proc.stdout or "[]")


# --------------------------------------------------------------------------- apply


def render(actions: list[dict], warnings: list[str], cards: dict[int, dict]) -> str:
    lines = []
    for action in actions:
        cid = action["card"]
        title = cards.get(cid, {}).get("title", "")[:64]
        arrow = (
            f"{action['from']} → {action['to']}"
            if action["from"] != action["to"]
            else action["to"]
        )
        detail = action["reason"]
        if action.get("pr"):
            detail += f" PR #{action['pr']}"
        if action.get("unblocked_by"):
            detail += " by " + ", ".join(f"#{b}" for b in action["unblocked_by"])
        lines.append(f"[#{cid}] {arrow} ({detail}) — {title}")
    for warning in warnings:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def publish_report(actions: list[dict], cards: dict[int, dict]) -> None:
    """Human-readable run summary in the Actions UI / terminal; nothing when nothing happened."""
    if not actions:
        return
    text = render(actions, [], cards)
    print(text)
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        rows = ["| card | from → to | why | title |", "|---|---|---|---|"]
        for action in actions:
            rows.append(
                f"| #{action['card']} | {action['from']} → {action['to']} | {action['reason']} | "
                f"{cards.get(action['card'], {}).get('title', '')[:80]} |"
            )
        Path(summary).write_text(
            "### Board sync\n\n" + "\n".join(rows) + "\n", encoding="utf-8"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Keep the Vikunja board in step with GitHub pull requests, deterministically."
    )
    parser.add_argument(
        "--policy", default=str(Path(__file__).with_name("policy.json"))
    )
    parser.add_argument("--repo", help="owner/name; defaults to $GITHUB_REPOSITORY")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write changes; without it this is a dry run",
    )
    parser.add_argument(
        "--json", action="store_true", help="machine-readable report on stdout"
    )
    parser.add_argument(
        "--check-title", metavar="TITLE", help="validate one PR title and exit"
    )
    parser.add_argument(
        "--prs-file", help="read pull requests from a JSON file instead of GitHub"
    )
    args = parser.parse_args(argv)

    if args.check_title is not None:
        problems = check_title(args.check_title)
        if problems:
            print("pull-request title does not name its card:")
            for problem in problems:
                print(f"  - {problem}")
            print("\nexample: [#18] s0-t4: implant bonuses on top of base attributes")
            return 1
        return 0

    policy = {
        **DEFAULT_POLICY,
        **json.loads(Path(args.policy).read_text(encoding="utf-8")),
    }
    repo = args.repo or os.environ.get("GITHUB_REPOSITORY", "")
    if not repo and not args.prs_file:
        raise SystemExit("no --repo and no GITHUB_REPOSITORY")

    board = Vikunja(
        os.environ.get("VIKUNJA_URL", "http://127.0.0.1:3457/api/v1"),
        int(policy["project_id"]),
        int(policy["view_id"]),
        token=os.environ.get("VIKUNJA_TOKEN") or None,
    )
    cards = board.cards()
    columns = board.columns()
    prs = (
        json.loads(Path(args.prs_file).read_text(encoding="utf-8"))
        if args.prs_file
        else fetch_prs(repo, os.environ.get("GITHUB_TOKEN"), int(policy["max_prs"]))
    )

    actions, warnings = desired_state(cards, columns, prs, policy)

    if not args.apply:
        if args.json:
            print(
                json.dumps(
                    {"dry_run": True, "actions": actions, "warnings": warnings},
                    ensure_ascii=False,
                    indent=1,
                )
            )
        else:
            publish_report(actions, cards)
            if warnings:
                print(render([], warnings, cards))
        return 0

    for action in actions:
        if action.get("set_done"):
            board.set_done(action["card"])
        if action["to"] != action["from"]:
            board.move(action["card"], action["to"])

    # Recompute from the board as it is now: a clean sync leaves nothing to do.
    residual, _ = desired_state(board.cards(), board.columns(), prs, policy)
    if residual:
        print("board did not converge; still pending:", file=sys.stderr)
        print(json.dumps(residual, ensure_ascii=False, indent=1), file=sys.stderr)
        return 1

    publish_report(actions, cards)
    if args.json:
        print(
            json.dumps(
                {"applied": actions, "warnings": warnings}, ensure_ascii=False, indent=1
            )
        )
    if warnings and not actions:
        print(render([], warnings, cards))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
