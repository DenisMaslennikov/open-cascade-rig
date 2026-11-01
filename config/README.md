# config — the canonical default, not the running settings

Two places hold configuration, and they are not the same thing:

| Where | What | Versioned |
|---|---|---|
| this directory (`cascade-rig/config/`) | the **canonical** prompts, the plan schema, and `*.example.yaml` files with the recommended roles and models | yes — a change here is a pull request |
| `~/.config/cascade/` | the machine's **live** settings: the actual project files, `keys.env`, and the pricing cache | no — secrets and per-machine state |

Why the split: a model choice is a decision about the pipeline, so it belongs in the repository with
an ADR or an evidence note behind it. A token and a machine path do not belong in a repository at
all. Keeping one copy in each place without saying which is which is how the two silently drift —
observed in this rig within a day of the first import.

## Files

- `prompts/*.md` — canonical role prompts. Live copies are symlinks to this directory
  (`bin/config_init.py --link`), so editing a prompt is a commit, not a local edit that vanishes.
- `schema/plan.schema.json` — the plan contract, also symlinked into the live config.
- `roles.example.yaml` — sampling parameters per role; `max_tokens: null` means "no output cap".
- `projects/<slug>.example.yaml` — which model serves which role for one project, with a comment per
  choice. `bin/config_init.py --materialize --project <slug>` copies it to
  `~/.config/cascade/projects/<slug>.yaml` if that file does not exist yet.

## When a model changes

1. Measure it (`playbook/costs.md`), record the run in `evidence/`.
2. Update `projects/<slug>.example.yaml` — the example **is** the recommendation, so the next project
   starts from what currently works.
3. If the change is a policy (a reviewer must not share a vendor with the executor, say), write the
   ADR; a price-driven swap of one model does not need one.
4. `bin/config_init.py --check` must report the live project file as an intentional deviation, not a
   silent one.
