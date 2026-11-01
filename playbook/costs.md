# Costs

**When to read:** before a run that costs money, and whenever a phase "takes forever".

## What actually costs

Not the diff, the conversation. An agent role resends its whole context on every turn, so cost grows
with iterations, not with the size of the change. A reviewer agent that reads a repository for thirty
turns pays for the repository thirty times.

Measured on this rig (gateway prices, dollars per million tokens):

| Model | input | output | Note |
|---|---|---|---|
| `~deepseek/deepseek-v4-pro-latest` | 41.61 | 124.83 | the "cheap" reviewer was 30x the flash executor |
| `~deepseek/deepseek-v4-flash-latest` | 0.94 | 6.53 | executor candidate |
| `~z-ai/glm-flash-latest` | 1.37 | 1.76 | writes and finishes; cheap |
| `qwen/qwen3.8-27b` | 0.42 | 23.04 | cheap input, dear output: good for reading |
| `openai/gpt-5.4-mini` | 0.75 | 4.50 | cheap both ways, strong on code |

## Procedure

- Record every call: `bin/usage.py record ...` writes to the project ledger; nothing is "closed" until
  its cost is recorded.
- Report per phase: `bin/usage.py report -p <project> [--feature F] [--task T] [--json]`.
- Put the cost into the commit body of the task: tokens and money per role, plus a separate line for
  the wasted iterations of pipeline debugging (failed runs, kill-by-budget, empty answers).
- Give the owner the total in the gateway's currency after every task, per role and overall.

## Traps

- **A budget in the agent runtime is not a budget in money you expect.** The runtime prices tokens
  with the gateway's per-model numbers, which can be far above the vendor's list price; a `$2` budget
  aborts a run whose real cost would have been cents elsewhere, and a `$25` one lets a single review
  burn $26.
- **Retry loops are invisible in the ledger until they finish.** A run killed by budget records usage,
  a run killed by hand often does not. Check `state/…/roles/<role>/<ts>/record.json`, not only the
  ledger.
- Reasoning models spend output budget on thinking: an empty answer at `finish_reason=length` is a
  cost problem, not a broken gateway.
