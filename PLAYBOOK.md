# PLAYBOOK — how to run the rig

One chapter per area. Each chapter states when to read it, the procedure, and the traps with their
symptoms — a trap without a symptom is a mood, not knowledge.

| Chapter | Covers | Read before |
|---|---|---|
| `playbook/running-roles.md` | running a role as an agent: toolset, `--verify`, `--allowed`, logging | starting any role |
| `playbook/gates.md` | `check-diff`, `frozen-tests`, `expect-fail`, `expect-pass` | freezing tests, running the executor |
| `playbook/board.md` | the Kanban as the status tool: columns, archive, the done-bucket trap | every phase transition |
| `playbook/review-loop.md` | two reviewers, read-only review, the fix loop, escalation | before any review |
| `playbook/costs.md` | what a run costs and why, how to price a phase, how to record it | before a run that costs money |
| `playbook/model-behaviour.md` | what each model actually does inside a loop | routing work to a model |
| `playbook/ci-and-runners.md` | hooks, self-hosted runner, who may run what | touching CI |

Rules of maintenance:

- A trap goes in the chapter of the area it belongs to, never into a "misc" file.
- A factual claim about a run cites an `evidence/` note; without it the sentence is a hypothesis.
- A decision goes to `adr/`; a procedure goes here. The same thing in both places is a link, not a copy.
