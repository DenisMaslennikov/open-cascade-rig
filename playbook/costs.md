# Costs

**When to read:** before a run that costs money, and whenever a phase "takes forever".

## What actually costs

Not the diff, the conversation. An agent role resends its whole context on every turn, so cost grows
with iterations, not with the size of the change. A reviewer agent that reads a repository for thirty
turns pays for the repository thirty times.

## Price source: OpenRouter, fetched weekly

The numbers in this chapter are **tokens** plus cost priced by OpenRouter — the public list price of the
same models. The local gateway prices its routes with overrides that are 30-70x the list price and are
not the currency the owner budgets in, so quoting them produced a "$93 for a day" figure that was both
wrong and alarming. Do not copy gateway prices into this repository.

```
cd bin && python3 -m rig.prices_openrouter --refresh            # weekly job; needs the LAN SOCKS proxy
cd bin && python3 -m rig.prices_openrouter --report --days 1    # reprice the ledger with OpenRouter prices
```

The catalogue is cached in `~/.local/state/cascade/pricing-openrouter.json` — data, not knowledge, so it
stays out of the repository. The fetch goes through `socks5h://192.168.31.6:10808`; a direct request to
openrouter.ai is answered with 403 from this host.

## List prices, OpenRouter, USD per million tokens

| Model | input | output |
|---|---|---|
| `~anthropic/claude-opus-latest` | 4.00 | 20.00 |
| `~anthropic/claude-sonnet-latest` | 2.00 | 10.00 |
| `qwen/qwen3.8-max-0902` | 2.00 | 6.00 |
| `moonshotai/kimi-k3.1-pro` | 2.00 | 10.00 |
| `~deepseek/deepseek-v4-pro` | 0.115 | 4.00 |
| `~deepseek/deepseek-v4-flash-latest` | 0.043 | 0.30 |
| `~z-ai/glm-latest` | 0.070 | 1.40 |
| `~z-ai/glm-flash-latest` | 0.029 | 0.09 |
| `minimax/minimax-m3.1-flash` | 0.150 | 0.60 |

## Procedure

- Record every call: `cd bin && python3 -m rig.usage record …` writes to the project ledger; nothing
  is "closed" until its cost is recorded.
- Report per phase: `cd bin && python3 -m rig.usage report -p <project> [--feature F] [--task T]
  [--json]`, or `cd bin && python3 -m rig.prices_openrouter --report` for a priced view.
- The **run report carries two tables, and both are produced by the tool, never by hand**:

  ```
  cd bin && python3 -m rig.cost_report --project <slug> --days N
  ```

  Table 1 is the **successful runs** — a run record that reached `done`. Table 2 is the **failed
  attempts** — records that hung, were killed or ended `failed`, plus the window's ledger spend that
  no successful run explains. Both tables carry the same columns: role | model | input tokens |
  output tokens | cache-read tokens | cost at the gateway's prices | cost at OpenRouter list prices.
- Success and failure are read from `record.json` (`state == "done"`, plus the `result.usage` block),
  **not** from whether the worktree changed. A run that wrote files and was killed is a failed
  attempt, and a run that changed nothing and exited `done` is a success.
- **The gateway column is for the owner's eyes only.** The repository, the commits and the PR body
  carry OpenRouter list prices and nothing else — the gateway's overrides are 30–70x the list price
  and quoting them produced the "$93 for a day" figure.
- In a **PR body** the cost table names its source in the column header — `Cost, $ (OpenRouter
  list)` — and its caption names the catalogue snapshot date (`pricing-openrouter.json`,
  `fetched_at`) and says this is the list price, not the gateway invoice. The numbers must match
  `cd bin && python3 -m rig.prices_openrouter --report --project <slug> --days <window>` for the
  card's window.
- Put the cost into the commit body of the task: tokens per role and model, and the OpenRouter-based
  cost with the source named. The owner gets the same numbers in the chat and in Telegram, including
  the failed-attempts table — see `reporting.md` for what the report says and in which language.

## Traps

- **A budget in the agent runtime is not a budget in money you expect.** The runtime prices tokens with
  the gateway's per-model numbers, which can be far above the list price; a budget that looks absurdly
  large may still abort a run, and a budget that looks small may allow a very expensive one.
- **Read success and failure from `record.json`, not from the worktree.** A hand-killed run often has
  no ledger lines at all until it is killed, and ledger lines alone cannot tell a success from a
  failure — `state/…/roles/<role>/<ts>/record.json` is the only source of `state`. The failed table of
  `rig.cost_report` exists so that this check is a command, not a memory exercise.
- Reasoning models spend output budget on thinking: an empty answer at `finish_reason=length` is a cost
  problem, not a broken gateway.
- Cache reads are counted but not priced in any table — OpenRouter does not publish a per-route cache
  rate, and inventing one would make the total look better than it is.
