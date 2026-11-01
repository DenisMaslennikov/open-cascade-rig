# What the models actually do

**When to read:** when choosing a model for a role, or when a run "does nothing".

Behaviour inside an agent loop does not match a single chat call. Observed on this rig:

| Model | In a loop | Use it for |
|---|---|---|
| `glm-flash` | writes many files, finishes, cheap; on a brief without a red gate it can research for nine minutes and write nothing | executor, specifier on a tightly scoped brief |
| `deepseek-v4-pro` | reads a lot, strong on review findings; as an agent it is priced per turn and expensive | reviewer over a plain diff, not as a loop |
| `qwen3.8-max` | strong and slow; as an agent it read the repository for five minutes and produced no report | final review through pr-agent, not as an agent |
| `glm-latest` (reasoning) | at a 32k output cap it can return `content: null` — the budget went to `reasoning_tokens` | architect, with a generous or absent output cap |

## Rules that came out of this

- Never cap the output of a reasoning role in code; put the limit in the role config or leave it open.
  An empty `content` with `finish_reason=length` is truncation, not a broken key.
- Route a slow strong model to a single-shot call over a prepared artefact (diff, document), not to a
  tool loop.
- One reviewer vendor must differ from the executor's and, ideally, from the other reviewer's.
- If a role produces no file changes twice in a row, change the task (narrower, with a failing check),
  not the model. "Use a smarter model" has not fixed a single one of these runs.
