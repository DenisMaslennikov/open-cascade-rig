# workflow — process

When to read: before starting any task, and before opening a pull request.

## Branches and merge

- `main` — through pull requests only: squash, linear history. **A human merges; never an agent, a
  bot or a scheduled job.** In a single-owner repository the author cannot approve their own pull
  request, so an approval counter would only ever be bypassed by an administrator merge. The
  human-only merge plus a visible reviewer report replace the counter.
- `feat/<slug>` — an integration branch for a larger change; lives at most a week.
- `task/<feature>/<id>` — atomic subtasks; their pull request targets `feat/*`, never `main`.
- Never rewrite published history. `pre-push` refuses it; the repository ruleset refuses it again.

## Who writes the rig's own code

The rig develops itself: a change to `bin/`, `config/` or the gates reaches `main` as a card that the
**cascade** executes — plan, frozen tests, implementation, review. The orchestrator (Hermes) opens
cards, arbitrates and merges; it does not hand-write pipeline code except in a declared emergency, and
then the change is reported as such in the pull request.

Why: a pipeline patched by hand has never been tested by itself, so the first task that depends on the
patch is the test. Infrastructure that the pipeline cannot own yet (hooks, CI wiring) is the exception
and is written by the orchestrator with a card recording the debt.

## Cards

1. One card = one subtask = one `files_allowed` list.
2. A card states the acceptance: the command that must turn from red to green.
3. A card that cannot be taken yet is blocked by the card that unblocks it — a pull request in flight
   blocks its card until it is merged, because work against unmerged code is work against a guess.
4. Nothing is "done" until the pull request is merged, the board says so, and the cost is on the card.

## Gates (the scripts live outside the repository)

```bash
CASCADE=~/.config/cascade/bin
STATE=~/.local/state/cascade/<project>/task.json
ALLOWED=$(python3 -c "import json;print(','.join(json.load(open('$STATE'))['files_allowed']))")

python3 $CASCADE/gate.py frozen-tests  --repo . --base origin/feat/<slug>
python3 $CASCADE/gate.py expect-fail   --repo . --cmd "python3 -m pytest"
python3 $CASCADE/gate.py check-diff    --repo . --base origin/feat/<slug> --allowed "$ALLOWED"
python3 $CASCADE/gate.py expect-pass   --repo . --cmd "python3 -m pytest"
```

`files_allowed` comes from the task state, not from a file in the repository.

## Review before merge

- A pull request carries the reviewer's report: findings, and separately the points only a human can
  settle. A merge without that report is a merge nobody reviewed.
- The reviewer is advisory: it cannot block on its own and cannot merge.
- The human merging decides what is fixed now, what becomes a card, and what is rejected — with the
  reason written in the pull request.
- The reviewer is a different vendor from the implementer. Review reports are internal: they are not
  published to GitHub.
