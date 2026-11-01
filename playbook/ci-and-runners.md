# CI, hooks and runners

**When to read:** before touching `.github/`, `.githooks/`, or anything that decides who may run what.

## Hook layout

- Hooks are versioned in the repository (`.githooks/`) and enabled per clone with
  `git config core.hooksPath .githooks`.
- `pre-commit` runs `ruff check --fix` and `ruff format` on the staged Python files and re-stages the
  result; an unfixable lint error blocks the commit.
- `pre-push` delegates to the machine-wide hook, which rejects non-fast-forward, branch deletion and a
  push to `main`. Without that shim, switching `core.hooksPath` silently disables the force-push ban.

## Runners

- GitHub-hosted jobs are the default pipeline: lint, tests, schema gate, frontend, ADR lint.
- A self-hosted runner (`[self-hosted, cascade]`, ephemeral container per job) runs the same checks in
  our own environment. It is gated to the owner's branches and non-fork pull requests, because it
  executes repository code on our machine.
- For checks that must run "in the cloud only after the owner approves", the mechanism is a GitHub
  `environment` with required reviewers on that job: the job sits in `waiting` until the approval.
  There is no other "after approval" switch in GitHub.

## Traps

- A required status check is referenced by its check name; renaming a job's `name:` can orphan branch
  protection rules. Rename display names only when no rule references them.
- Hook interpreters must be chosen by *running* them: a worktree has no `backend/.venv`, so an
  existence check picks an interpreter that cannot execute.
- The executor gate does not run the formatter; the hook and CI do. A green executor with a red CI is
  a missing step, not bad luck.
