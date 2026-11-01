# tests

One file per module under test, mirroring `bin/`: `bin/prices_openrouter.py` → `tests/test_prices_openrouter.py`.

The rules are in `.agents/rules/testing.md`: pytest for structure, `hypothesis` for input generation,
`mutmut` for the "could this test ever fail" question. No network and no real gateway — subprocess and
`urllib` are patched at the boundary.

The suite is written card by card; a module without a test file is a known gap, tracked on the board as
tech debt, not an invitation to skip the test.
