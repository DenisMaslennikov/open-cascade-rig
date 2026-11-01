# Gates

**When to read:** before freezing tests, before running the executor, and whenever a check is green
for a reason you did not predict.

The gates are deterministic (`bin/gate.py`) and answer four questions:

- `check-diff` — is every changed file inside the subtask's `files_allowed`?
- `frozen-tests` — did the executor touch the tests it was supposed to make pass?
- `expect-fail` — do the tests fail on the stub? A gate that is green here means the work is already
  done, and the executor will stop.
- `expect-pass` — do they pass on the implementation?

## The red gate must be red for the right reason

An `ImportError` is not a red gate: it proves the tests cannot run, not that they test behaviour.
The stub must exist and raise `NotImplementedError`.

Observed on this rig: a plan that split tests and implementation into two subtasks produced no stubs
at all — `specify_task.py` only emits files listed in *that* subtask's `files_allowed` — so the red
gate was green on `ImportError`. Remedy: run a specifier pass over the implementation files that adds
stubs raising `NotImplementedError`, then the same gate is red for the right reason.

## When a guard lands after the freeze

Adding a test after the code exists is allowed, but the red proof is then historical and has to be
reconstructed: `git show <old-commit>:path > file`, run the new tests, confirm they fail on exactly
the new cases, restore the file. Without that, the test is an assertion about current behaviour.

## Traps

- `git status --porcelain` parsing by offset truncates the first character of a path (`docs/…` →
  `ocs/…`) and produces a fake out-of-scope verdict. Use `git diff --name-only` plus
  `git ls-files --others`.
- The executor gate does not run `ruff format`; CI does. Formatting drift therefore shows up as a red
  CI run after a green executor — either run format in the gate or add the pre-commit hook.
- A formatter or lint fix pass can be needed after a review fix; it counts as part of the same
  subtask, not as scope creep.
