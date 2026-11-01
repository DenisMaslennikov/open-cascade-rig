# AGENTS.md — index of what a worker must know

Read this first, then only the files the task actually touches. Everything here is binding.

| Area | File | When to read |
|---|---|---|
| Decisions (why the pipeline is shaped this way) | `DECISIONS.md` → `adr/*.md` | before changing a contract, a role boundary or the gates |
| Operating knowledge (how to run it) | `PLAYBOOK.md` → `playbook/*.md` | before running any phase, and before debugging one |
| Evidence (what a run cost and taught) | `evidence/` | when a claim about cost or model behaviour needs backing |
| Roadmap and open questions | `PLAN.md` | before picking the next task |
| Scripts | `bin/` | the only way to run a phase; no manual steps |
| Configuration | `config/` + `config/README.md` | canonical prompts, plan schema, `*.example.yaml`; the live settings are in `~/.config/cascade`, symlinked back to this directory |

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

## Language

Code, commits, ADRs, chapters and evidence notes are English. Conversations with the owner and the
final summary of a task are Russian. A Russian sentence in a chapter is a defect; an English
paragraph in a chat reply is noise.

## Definition of done for a task

- the checks it names are green (tests, lint, mutation on the touched module, SAST);
- the card on the board is in the right column, not "somewhere in Backlog";
- one evidence note if the run taught something, and one chapter edit if a rule changed;
- a commit body that says what was done, how it was verified, what it cost, and what remains.
