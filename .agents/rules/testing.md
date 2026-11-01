# testing — tests

When to read: before writing or editing tests, and before diagnosing a failing one.

## Where and how

- `tests/` at the repository root mirrors `bin/`: `bin/rig/prices_openrouter.py` → `tests/test_prices_openrouter.py`.
- No network, no real gateway, no real git remote in a unit test: subprocess and `urllib` are patched
  at the boundary. A test that needs the internet is an integration test and belongs in its own file.
- A test name states the behaviour (`test_reports_direct_connection_when_no_proxy_is_configured`), not
  the function name.
- One test — one scenario; shared fixtures go to `tests/fixtures/`.

## Property tests, not examples

The same library set as the product repository: **pytest** for structure, **hypothesis** for input
generation, **mutmut** for the "can this test fail at all" question. There is no frontend here, so no
fast-check and no Stryker.

- Every module that parses, prices, formats or decides gets at least one `@given`-driven test. A table
  of three examples is not a property: state the invariant and let hypothesis hunt for the counterexample
  (`cost is never negative`, `the same tokens and price always reprice to the same number`).
- A test that can only pass — `assert result is not None`, `assert isinstance(x, dict)` — is a test that
  cannot fail. Assert the value the specification names.
- A fixed expected value must come from the rule in the card, never from running the implementation and
  pasting the output. Copying the output freezes the bug.

## Order

1. First a test that fails on the old code (`gate.py expect-fail`).
2. Then the implementation until green (`gate.py expect-pass`).
3. While the `Code` phase runs, tests are frozen: `gate.py frozen-tests` blocks edits under `tests/**`.

## Mutation

- `make mutation MUTATE=bin/<module>.py` after a change to that module. A surviving mutant is either a
  missing assertion or code that does not matter — decide which, and write it in the pull request.
- A whole-repository mutation run is minutes of CPU for a signal nobody reads; it is not a gate.

## Prohibitions

- Bending a test to fit the implementation (or the reverse) is a dispute for the arbiter, not a right
  of the code.
- Testing the implementation instead of the behaviour (private calls, call order).
- Asserting types or constants the language or a library already guarantees.
