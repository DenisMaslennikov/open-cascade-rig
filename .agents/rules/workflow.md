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

## Frameworks before wheels

Decomposition is the step where a wheel is either bought or invented, so it is the step that must ask.
When a task touches a boundary the ecosystem already owns — HTTP, an ORM, migrations, scheduling,
queues, authentication — the architect proposes the ready-made options to the owner **before the plan
is accepted**: two or three, including "write it ourselves", one line of what each buys and one line of
what each costs, the recommendation first. A plan that hand-rolls a standard component without asking
is a plan defect and is rejected as one; it is not a discussion for code review.

Deciding is the owner's: "propose" never means "add the dependency silently". The stack the owner has
already fixed is a constraint, not a question — see `DECISIONS.md` D20 (FastAPI, SQLAlchemy, Alembic,
SQLite as the draft database). Re-opening a settled choice costs a question the owner has already
answered.

## Tech debt

Debt is not accumulated. A defect found while doing a task is fixed **right here, right now** — same
card, same pull request, same review — whenever the fix does not need a large-scale change: a broken
path, a missing gate, a false failure of a script. Such a fix is made immediately, in the branch that
is open, without asking. Finding a problem and writing it down for "later" is the expensive option, not
the safe one: the next reader pays again, and the branch that would have carried the fix is already
merged.

Ask the human, before finishing the task, when the fix cannot live in the current task — above all when
it would touch many files, because a change that spreads across the repository is no longer "here and
now"; also when it changes a contract, a schema or a rule another task depends on, or needs a decision
the task does not own. The ask proposes the card and states what the defect is, why it cannot be fixed
inside the current task, the options with a recommendation first, and what happens if it is left alone.
Send the ask where an answer can actually arrive — the chat, and Telegram when someone is there to read
it; a question nobody will read is a question not asked, so when the channel cannot answer, ask in the
chat only.

A card is created **only with the human's permission**. Writing a card to avoid work is the failure
mode, and so is silently dropping the finding: a card is the record of a decision the human made, not a
licence to leave work undone.

When debt is deliberately deferred by that decision, it must be visible, not silent:

- the gate prints it (a `WARNING` rule, an ignore with the reason in the same line), and
- the card that removes it is named where the suppression lives (`Card #NNN`), so deleting the
  suppression is a measurable task rather than a memory.

Traps, and what they look like from outside:

- a TODO or a follow-up card written instead of a fix — the defect ships, and the "later" card outlives
  the memory of why it was opened;
- a finding dropped without a decision — nobody decided anything, and the same defect re-surfaces on
  the next branch;
- a small fix waiting on a question — a one-line correction idles while a conversation could have been
  skipped;
- the ask sent into a channel that cannot answer — the task stalls on an answer that was never coming.

## Cards

1. One card = one subtask = one `files_allowed` list.
2. A card states the acceptance: the command that must turn from red to green.
3. A card that cannot be taken yet is blocked by the card that unblocks it — a pull request in flight
   blocks its card until it is merged, because work against unmerged code is work against a guess.
4. Nothing is "done" until the pull request is merged, the board says so, and the cost is on the card.

## Limits

**No limits by default.** No cost cap, no reasoning cap, no phase timer on a role run: limits are decided
later, from measured statistics, each as its own card. Reasoning that looks expensive is not by itself a
reason to stop a run — a reasoning model spending its output budget on thinking is normal behaviour, not
a loop.

- `run_role.py --budget` exists as an operator choice and is off unless given; when given it is in
  OpenRouter list dollars and is scaled into the currency the runtime bills in, so the number means what
  it says.
- What remains is loop protection, not policy: `--iterations` and `--timeout` stop a run that is not
  progressing. Changing them is a card like anything else.
- Statistics before limits: `usage.py` and the weekly OpenRouter report accumulate tokens, reasoning
  tokens and empty answers. A future limit cites that data.

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
