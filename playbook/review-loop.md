# Review loop

**When to read:** before any review, and whenever a review finds "nothing".

## Two reviewers, one purpose

- `reviewer` — cheap and fast vendor, on the diff of every subtask.
- `reviewer_final` — stronger vendor, on the whole pull request, with a longer wait.

The two must not share a vendor with the executor: a reviewer that thinks like the author has the
same blind spot.

## Procedure

```
cd bin && python3 -m rig.review_fix_loop --project <slug> --plan plan.json --subtask <id> --worktree <wt> \
  --engine pr|agent [--role reviewer|reviewer_final] [--max-rounds 3] [--fix-model <flash>] [--notify]
```

1. Review. `--engine pr` is one pr-agent call over the plain diff — cheap, and the right default for
   a subtask. `--engine agent` is a read-only OpenHands reviewer that can read the repository; it is
   priced per turn, so it is for the final PR or for a diff that cannot be judged alone.
2. Blocking findings (`blocker`, `major`) go back to the executor via
   `cd bin && python3 -m rig run_subtask --feedback …` (the review-fix pass). `minor` never starts a
   round: fixing what is not broken is work for work's sake.
3. Re-review. Clean → done. Three rounds with blockers left → escalate to the owner in Telegram, in
   Russian, listing each round, what was fixed (commit), and what still blocks.
4. Reviews are **not** posted to GitHub. The owner reads them in the console and Telegram.

## What a review must not do

- Invent findings to look thorough. An empty review is a valid review; the «что проверить человеку»
  section is optional and stays empty when nothing needs a human.
- Accept a test aimed at the code, or code aimed at the tests. Both are blockers; mutation testing
  is the mechanical way to catch the second.
- Edit the branch under review. The read-only gate exists for that reason.

## Cost reality

Reviews are the most expensive part of the loop when done by an agent: each turn resends the whole
conversation, so a review of one four-file diff cost ~544k input tokens across 15 calls, against ~8k
tokens for the same diff through pr-agent. Price those tokens with
`cd bin && python3 -m rig.prices_openrouter --report`; do not quote local gateway prices, which are
30-70x the list price. See `costs.md`.
