# The board is the status tool

**When to read:** at the start of every session, and on every phase transition.

The Kanban is the only place the owner looks to see what is left. A card that is stale is not a
cosmetic problem; it is the wrong status shown to the person paying for the work.

## Procedure

- Start of session: `bin/board_archive.py --project <slug> --status` — columns, card count, orphans
  (cards in no bucket), archived count.
- Each phase moves its own card, in the same action as the work: plan ready → `Plan`, tests frozen →
  `Tests`, executor green → `Code`, review running → `Review`, merged → archived.
- Archive = `done` + the `Done` column, in one idempotent call:
  `bin/board_archive.py --project <slug> --cards 15,16 --note "PR #3 merged"`. It re-reads the board
  afterwards instead of trusting the response code.
- A card that is not the current one stays where it is: moving it "for neatness" destroys the history
  the owner reads.

## Traps

- **`done_bucket_id` of a kanban view defaults to the second column.** Marking a card done teleports
  it into that column, and moving it out silently clears `done` — the board says `Done: 0` while
  finished cards sit in `Tests`. Fix once per view:
  `POST /projects/P/views/V {"done_bucket_id": <Done>, "default_bucket_id": <Backlog>}`;
  `board.py create_project` now sets both.
- **The view endpoint returns at most 50 tasks per column per page**; `?page=2` continues the same
  column. One response cannot prove a card is missing, and a naive reader declares live cards
  "orphans".
- A card in no bucket is invisible on the board entirely. `board.py create` moves it itself; cards
  created around the adapter do not. `--orphans --to Backlog` puts them back.
- Renaming a column breaks `board.py` (it resolves buckets by title) and any saved filter; rename the
  card, not the column.
