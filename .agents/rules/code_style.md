# code_style — code style

When to read: before editing anything under `bin/`, `config/` or `tests/`.

## Written artefacts

- A comment exists only where the code cannot state its intent: a non-obvious workaround, a quirk of
  an external tool, a deliberate deviation. Everything else belongs in the docstring.
- A docstring describes behaviour, arguments, return value and raised errors. It never contains a call
  example — an example duplicates the signature and rots.
- Every remark and every comment about the project, and every description written into a file, is
  English: review findings and review summaries, the pull-request body, the commit body, the plan and
  evidence notes, the descriptions inside JSON artefacts, the comments in code and in configuration.
- Russian is the language of the report to the owner in the chat and in Telegram, and of nothing else.

## Python

- Types on every public boundary; `Any` only where an external payload is genuinely untyped, and then
  narrowed immediately.
- `ruff` decides formatting and lint (`pyproject.toml` at the repository root, line length 120);
  `ruff format` is the only formatter.
- A script has one job and a `main()` that returns an exit code; `raise SystemExit("ERROR: …")` is for
  "the operator must fix something", a return code for "the answer is no".
- Subprocess calls are explicit argument lists, never `shell=True` with an interpolated string.

## Environment

- **No environment-specific defaults in code.** An address, a proxy, a hostname or an absolute home
  path written as a fallback works on exactly one machine and hides the fact that the machine was
  never configured.
- Resolve in this order: the project config (`~/.config/cascade/projects/<slug>.yaml`), then an
  environment variable (or `keys.env`), then fail with an instruction naming what to set.
- A missing proxy means *no proxy*, never a guessed one.
- `bin/rig/rig_conf.py` exists for this: use it instead of inventing another lookup.

## Imports

- Every import lives at the module top. **Local (function-level) imports are forbidden.**
- If a change seems to need one — a circular import, a heavy optional dependency, a shortcut in a
  test — stop and raise it for a human decision with the options: move the shared symbol to a module
  both sides can import, invert the dependency (the lower module defines a protocol, the higher one
  implements it), or split the module.
- Breaking a cycle by moving an import inside a function is not a fix: it hides the cycle and the next
  reader pays for it. This list of forbidden workarounds grows — a pattern the owner forbids is added
  here in the same pull request that introduces the alternative.

## Prohibitions

- No local imports (see above).
- No secrets, tokens, `keys.env`, price caches or downloaded catalogues in commits.
- No public behaviour change without a decision entry: a rule that changes the pipeline is a `D…`
  entry in `DECISIONS.md`, in the same pull request.
