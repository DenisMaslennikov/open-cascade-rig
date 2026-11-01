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
bin/prices_openrouter.py --refresh            # weekly job; needs the LAN SOCKS proxy
bin/prices_openrouter.py --report --days 1    # reprice the ledger with OpenRouter prices
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

- Record every call: `bin/usage.py record ...` writes to the project ledger; nothing is "closed" until
  its cost is recorded.
- Report per phase: `bin/usage.py report -p <project> [--feature F] [--task T] [--json]`, or
  `bin/prices_openrouter.py --report` for a priced view.
- Put the cost into the commit body of the task: tokens per role and model, and the OpenRouter-based
  cost with the source named. Give the owner the same list in the chat and in Telegram, including a
  separate line for wasted iterations.

## Traps

- **A budget in the agent runtime is not a budget in money you expect.** The runtime prices tokens with
  the gateway's per-model numbers, which can be far above the list price; a budget that looks absurdly
  large may still abort a run, and a budget that looks small may allow a very expensive one.
- **Retry loops are invisible in the ledger until they finish.** A run killed by budget records usage; a
  run killed by hand often does not. Check `state/…/roles/<role>/<ts>/record.json`, not only the ledger.
- Reasoning models spend output budget on thinking: an empty answer at `finish_reason=length` is a cost
  problem, not a broken gateway.
- Cache reads are not priced in the reports — OpenRouter does not publish a per-route cache rate, and
  inventing one would make the total look better than it is.
