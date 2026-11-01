# Running a role as an agent

**When to read:** before running any intellectual role (architect, specifier, designer, reviewer,
chief architect) or the executor on a small chore.

## Why a role is an agent and not one chat call

A role has to read the repository, write files, run its own check and correct itself. A single
completion cannot do that: the model answers prose about code it has not opened.

## Procedure

```
bin/run_role.py --project <slug> --role <role> --request @brief.md --worktree <wt> \
  [--allowed a.py,b.py] [--verify "<command>"] [--model M] [--iterations N] [--budget B]
```

- `--request` is a brief file: the task, the context, what counts as done. `@` matters.
- `--allowed` is the scope gate: files outside it are a violation and the run is discarded.
- `--verify` is the acceptance command, executed by the orchestrator after the run, not by the model.
  Exit 0 means done. It must be **red before the work** — see `gates.md`.
- `--read-only` (implied for `reviewer`, `reviewer_final`) forbids any workspace change: no file
  editor, and a post-run gate that fails the run if the tree moved.
- Every run writes `roles/<role>/<ts>/`: `prompt.md`, `system-prompt.md`, `log.txt` (noisy, OpenHands
  banners), `record.json`, `report.md` (the final message), `events.jsonl` + `transcript.md` (the whole
  conversation including `reasoning_content`). Without the last two, "why did the model do that" is
  guesswork.

## Traps

- **The prompt must go to `AgentContext(system_message_suffix=...)`.** `Agent(system_prompt=...)`
  replaces the tool protocol and the model stops calling tools. It then answers prose and looks
  "lazy".
- **A green acceptance before the work produces a no-op run.** Strong models read, call `finish`,
  return nothing. The executor's "you changed nothing" re-prompt only wastes a round.
- **A brief without a red acceptance stalls weak models.** Observed: `glm-flash` spent nine minutes
  on a four-file hook brief, wrote zero files, and had to be killed. Either give it a failing check
  or do the task by hand and say so.
- **Do not cap the output in the runner.** `max_tokens` belongs to the role config; a hard cap in
  code is how a reasoning model returns `content: null` with the whole budget spent thinking.
- **A role that may commit leaves a clean tree** at the end, so "changed files" reads as zero. Count
  `git diff --name-only <base>..HEAD` too, or a good run is recorded as a failure.
