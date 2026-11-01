# cascade-rig

A hand-driven rig for building and debugging a multi-agent development pipeline.

This repository is **not** the pipeline. It is the workbench where the pipeline is being figured out:
Hermes Agent as the orchestrator, OpenHands as the agent runtime, LiteLLM as the gateway, a Kanban
board as the projection of task state, and a pile of small scripts that wrap every step so that no
step depends on who ran it.

The name is deliberate. A *rig* is what you build when you want to test a hypothesis under load
before you commit to a design: a frame with the parts bolted on, wires hanging, and a dial you can
turn. `open-cascade` is the intended product; `cascade-rig` is what we are learning on.

## Why it exists

Building an agent pipeline is mostly a sequence of expensive surprises:

- a model that reads the artefact, calls `finish` and writes nothing;
- an acceptance command that is green before the work, so the agent has nothing to do;
- a reviewer that finds nothing because the diff is clean, and nobody notices the reviewer is broken;
- a "review" that costs $26 because a reviewer agent re-reads the repository every turn;
- a test that cannot fail, because it was written after the code and asserts what the code does.

Each of those is written down here as a rule, with the run that produced it. That is the point of the
repository: the knowledge is the deliverable, and it is versioned like code.

## Layout

```
bin/            the pipeline scripts — every phase is a script, never a manual step
config/         the canonical prompts, the plan schema, and *.example.yaml role/model defaults —
                no secrets, ever. The live settings live in ~/.config/cascade (see config/README.md)
adr/            architecture decisions in long form (why, alternatives, consequences)
DECISIONS.md    the index of decisions: number, one-line subject, status, link
PLAYBOOK.md     the index of operating knowledge
playbook/       one chapter per area: how to run it, and the traps that bite
evidence/       what a run actually cost and what it taught (one note per notable run)
docs/           runbooks and design notes
PLAN.md         phases and open questions
```

Runtime state is **not** in this repository: sessions, run logs, ledgers, board tokens and API keys
live outside it (`~/.local/state/cascade/`, `~/.config/cascade/`). Knowledge and code are versioned;
evidence of a run is distilled into `evidence/` and the raw logs stay where they were produced.

## How work happens here

Every change goes through a worktree, a branch, a pull request, and a human approval — including the
owner's own changes, because the point of the rig is to test the process on itself. There is no
"small enough to push to main".

```
git worktree add ~/worktrees/cascade-rig/<slug> -b <type>/<slug> origin/main
# work, run the checks, commit
git push -u origin <type>/<slug>
gh pr create --base main --fill
```

## Status

Pre-alpha, single operator, no users. The pipeline it explores is described in `DECISIONS.md` and
`PLAN.md`; the product it is being built for is a separate application, used here only as a test
range.

## Licence

AGPL-3.0-or-later. See `LICENSE`.
