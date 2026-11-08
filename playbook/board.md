# The board is the status tool

**When to read:** at the start of every session, and on every phase transition.

The Kanban is the only place the owner looks to see what is left. A card that is stale is not a
cosmetic problem; it is the wrong status shown to the person paying for the work.

## Procedure

- The board follows GitHub by itself: `tools/board/sync.py` (`.github/workflows/board.yml`) runs on
  every pull-request event and derives the whole state from the pull requests — PR open → `Review`,
  merged → `Done`, closed unmerged → `Plan`, and an unblocked `Backlog` card fills a free `Plan`
  slot, highest priority first — with `max_plan: null` in `tools/board/policy.json` that means every unblocked card, not a capped queue.
- A container card (a roadmap stage, a design brief) carries the `epic` label and is never moved by
  the sync — without the label a stage card would flood `Plan` as "unblocked"; `rig.board_seed`
  labels the stage cards it creates.
- A pull request names its card at the start of the title: `[#174] chore: …`. The `PR title names a
  card` job rejects a title without the `[#<card>]` prefix. Since GitHub appends `(#8)`, a bare
  `#8` in a title is the pull-request number and links nothing.
- Start of session: `cd bin && python3 -m rig.board_archive --project <slug> --status` — columns, card count, orphans
  (cards in no bucket), archived count. The sync repairs orphans and done-drift itself.
- Each phase keeps its card honest by opening its PR under the card's marker; the column follows.
- Archive = `done` + the `Done` column, in one idempotent call:
  `cd bin && python3 -m rig.board_archive --project <slug> --cards 15,16 --note "PR #3 merged"` — a manual fallback
  for a merged PR that carried no marker. It re-reads the board afterwards instead of trusting the
  response code.
- A card that is not the current one stays where it is: moving it "for neatness" destroys the history
  the owner reads.

## Traps

- **`done_bucket_id` of a kanban view defaults to the second column.** Marking a card done teleports
  it into that column, and moving it out silently clears `done` — the board says `Done: 0` while
  finished cards sit in `Tests`. Fix once per view:
  `POST /projects/P/views/V {"done_bucket_id": <Done>, "default_bucket_id": <Backlog>}`;
  project creation through the board library (`~/soft/vikunja/board.py`, called by
  `rig.board_seed`) sets both.
- **The view endpoint returns at most 50 tasks per column per page**; `?page=2` continues the same
  column. One response cannot prove a card is missing, and a naive reader declares live cards
  "orphans".
- A card in no bucket is invisible on the board entirely. Cards created around the adapter land
  there; `cd bin && python3 -m rig.board_archive --project <slug> --orphans --to Backlog` puts them back.
- Renaming a column breaks the board tools (they resolve buckets by title) and any saved filter;
  rename the card, not the column.
