# config — configuration and data

When to read: before changing anything under `config/`, and before adding a value a script reads.

## Two places, and which is which

| Where | What | Versioned |
|---|---|---|
| `config/` in this repository | canonical prompts, the plan schema, `*.example.yaml` with the recommended roles and models | yes — a change is a pull request |
| `~/.config/cascade/` | the machine's live settings: project files, `keys.env`, price caches | no |

`cd bin && python3 -m rig.config_init --check` reports drift; `--link` makes the live `prompts/`, `schema/` and `bin/`
symlinks back into the repository, refusing when a live copy differs so an edit cannot vanish.

## Rules

- A model choice is a decision: update `config/projects/<slug>.example.yaml` (the example **is** the
  recommendation), and record the reason — a comment on the line, an ADR when the choice is a policy.
- No secrets, no keys, no price caches in the repository. `pricing.json` and the OpenRouter catalogue
  are data: gitignored, regenerated, never committed.
- Prices quoted to the owner come from OpenRouter (`bin/rig/prices_openrouter.py`), never from the local
  gateway: the gateway's overrides are 30-70x the list price and are not the currency the owner budgets
  in.
- A value that differs per machine (proxy, gateway URL, repository path) is configuration, not a
  default in code — see `code_style.md`, "Environment".
