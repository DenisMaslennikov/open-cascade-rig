# AGENTS.md — index of what a worker must know

Read this first, then only the files the task actually touches. Everything here is binding.

| Area | File | When to read |
|---|---|---|
| Decisions (why the pipeline is shaped this way) | `DECISIONS.md` → `adr/*.md` | before changing a contract, a role boundary or the gates |
| Operating knowledge (how to run it) | `PLAYBOOK.md` → `playbook/*.md` | before running any phase, and before debugging one |
| Evidence (what a run cost and taught) | `evidence/` | when a claim about cost or model behaviour needs backing |
| Roadmap and open questions | `PLAN.md` | before picking the next task |
| Scripts | `bin/` → `bin/rig/` | the only way to run a phase; no manual steps. The entry point is `cd bin && python3 -m rig <tool>` — a tool name is a module of the `rig` package exposing `main()`; a per-script address like `bin/<tool>.py` died with card #191 |
| Configuration | `config/` + `config/README.md` | canonical prompts, plan schema, `*.example.yaml`; the live settings are in `~/.config/cascade`, symlinked back to this directory |
| Process rules | `.agents/rules/workflow.md` | before any task, and before opening a pull request |
| Code style | `.agents/rules/code_style.md` | before editing `bin/`, `config/` or `tests/` |
| Testing | `.agents/rules/testing.md` | before writing or editing tests |
| Configuration and data | `.agents/rules/config.md` | before touching `config/` or adding a value a script reads |

## Rules that are not negotiable

1. **Every phase is a script.** If a step needs a new operation, write the script first, then run it.
   A manual step is debt, not a completed phase.
2. **Acceptance is red before the work.** A green acceptance command before the work means the strong
   model reads the artefact and stops. Task requirements belong in the check, not in prose.
3. **Tests are frozen by someone other than the author of the code**, and a test written after the
   code must prove it can fail (`git show <old-commit>:path`, run, restore).
4. **Aiming code at tests, or tests at code, is a defect**, not a shortcut. Reviewers report it as a
   blocker; mutation testing is how it gets caught mechanically.
5. **The workspace is read-only for reviewers.** A reviewer that edits the branch under review has
   destroyed the evidence.
6. **Merges are human.** No agent approves or merges, no agent posts reviews to GitHub.
7. **Artefacts of the pipeline live outside the repository unless they are knowledge.** Logs, ledgers,
   run records: outside. Chapters, decisions, evidence notes: inside.
8. **Local imports are forbidden** in product code; a circular import is a question for the human
   with options, never a workaround with an import inside a function.
9. **No environment-specific defaults in code.** A gateway URL, a proxy, a hostname or a home path as
   a fallback works on one machine and hides that it was never configured. Project config → variable →
   fail with an instruction. `bin/rig/rig_conf.py` is the only resolver.
10. **The rig develops itself.** A change to `bin/`, `config/` or a gate becomes a card that the
   cascade executes; the orchestrator opens cards, arbitrates and merges. Hooks and CI wiring are the
   declared exception, written by the orchestrator with the debt recorded.
11. **No accumulated tech debt.** A problem found inside a task is fixed inside that task — same card,
   same pull request. If it cannot be (a contract or a rule another task depends on, a decision the
   task does not own, a cost past the task's budget), ask the human before finishing, with the options
   and a recommendation. A card records the human's decision; it is not a way to avoid asking.

## Commands

Hooks are versioned: `git config core.hooksPath .githooks` once per clone (the pre-commit hook refuses
to commit a staged file that also has unstaged edits, because the formatter would take them).

```bash
# Scripts are a package (`bin/rig`) and are run from `bin`: python3 -m rig <tool>, or
# python3 -m rig.<tool> for the same thing. The package is called `rig`, not `src`, because
# mutmut 3.8 reserves the `src.` prefix for itself (stats.py asserts).
python3 -m ruff check . && python3 -m ruff format --check .   # or: make lint / make fmt
cd bin && python3 -m pytest                                  # or: make test
cd bin && python3 -m pytest -m property                      # or: make property
cd bin && python3 -m mutmut run "rig.<module>.x_*"           # or: make mutation MUTATE=bin/rig/<module>.py
semgrep --config .semgrep.yml --severity ERROR --error       # the environment-defaults rule lives here
gitleaks detect --no-git --redact --source .                 # secrets, seconds
```

`make` may be missing on a developer machine; the commands above are the same gates without it.

## Language

Code, commits, ADRs, chapters and evidence notes are English. Conversations with the owner and the
final summary of a task are Russian. A Russian sentence in a chapter is a defect; an English
paragraph in a chat reply is noise.

## Definition of done for a task

- the checks it names are green (tests, lint, mutation on the touched module, SAST);
- the card on the board is in the right column, not "somewhere in Backlog";
- one evidence note if the run taught something, and one chapter edit if a rule changed;
- a commit body that says what was done, how it was verified, what it cost, and what remains.
