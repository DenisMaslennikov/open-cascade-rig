# Task report

**When to read:** when a card is done and the pull request is open — before saying "done" in the chat.

## Procedure

When a card is done, the owner gets a short report **in the chat and in Telegram**, in Russian. The
report says:

- what the pull request does;
- the difficulties met on the way;
- the compromises made (what was simplified or deferred, and why);
- the link to the open pull request.

The cost of the run goes into the same report — what the tables carry and in which currency is in
`costs.md`.

**Tests are part of the report.** One line per test, or per group of tests, naming what it covers:
the scenario, the boundary, the invariant. Not a list of file names — a reader who sees
`tests/test_plan.py` learned nothing. When the coverage is partial, the report carries one more line
saying what stayed uncovered.

## Two languages, two audiences

The pull request body and the commits are English only. The chat and Telegram report is Russian only.
Two languages, two audiences, no mixing: a Russian sentence in a commit or an English paragraph in the
Telegram report each defeat the point of having the other language.

## Traps

- **A report that names files instead of coverage.** `tests were added to tests/test_x.py` is not a
  test report; the owner cannot tell what behaviour is protected. Name the scenario, the boundary, the
  invariant.
- **A silent compromise.** Something was simplified to fit the budget or the scope, and the report
  reads as if nothing happened — the owner merges a decision that was never presented as one. State
  the compromise and its reason, or ask before making it.
- **The report without the link.** A "done" without the pull-request link sends the owner to look for
  the branch by hand; the link is part of the report, not an extra.
