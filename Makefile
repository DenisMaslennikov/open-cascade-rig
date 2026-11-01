# Every gate the pipeline runs, in one place: an agent types `make <target>` or runs the target the
# orchestrator named. Nothing here needs a database or the network except `semgrep` and `gitleaks`,
# which fetch their own rules/binaries once.

PY ?= python3
VENV ?= .venv

.PHONY: help venv lint fmt test property mutation semgrep gitleaks decisions ci

help:
	@grep -E '^[a-z-]+:' Makefile | sed 's/:.*//' | sort | column -c 100

venv:
	@test -d $(VENV) || $(PY) -m venv $(VENV)
	@$(VENV)/bin/pip install -q -r requirements-dev.txt

lint: venv
	@$(VENV)/bin/python -m ruff check .

fmt: venv
	@$(VENV)/bin/python -m ruff format .

test: venv
	@$(VENV)/bin/python -m pytest

# Property tests answering "does this hold for every input, not just the example in the test"
property: venv
	@$(VENV)/bin/python -m pytest -m property

# Mutation over the module a pull request touched: `make mutation MUTATE=bin/<module>.py`.
# A whole-repository run is minutes of CPU for a signal nobody reads, so the default runs one
# module (board_archive, the module of this pull request). mutmut 3.x has no --paths-to-mutate;
# the MUTATE path is translated into a mutant-name glob ("bin/<m>.py" -> "bin.<m>.x_*"),
# which `mutmut run` accepts as a positional argument.
#
# Debt, visible rather than hidden: `tests/` is empty today (the suite is written card by card),
# so pytest returns 5 ("no tests ran") and `mutmut run` exits 1 after generating the mutants.
# The `|| true` keeps the target usable until the card that adds `tests/test_board_archive.py`
# lands; delete the `|| true` in that card. `mutmut results` is never optional: a countable
# result must be printed.
MUTATE ?= bin/board_archive.py
MODULE_GLOB = $$(printf '%s' $(MUTATE) | sed -e 's|^bin/|bin.|' -e 's|\.py$$|.x_*|')
mutation: venv
	@$(VENV)/bin/python -m mutmut run --max-children 4 "$(MODULE_GLOB)" || true
	@$(VENV)/bin/python -m mutmut results

# ERROR findings block; WARNING findings are printed and tracked on the board (see .semgrep.yml).
semgrep:
	@semgrep --config .semgrep.yml --severity ERROR --error --quiet

gitleaks:
	@gitleaks detect --no-git --redact --source . || true

# DECISIONS.md is the decision log; a decision without a heading or a date is not a record.
decisions:
	@grep -qE '^### D[0-9]+\.' DECISIONS.md || { echo "DECISIONS.md: no D-numbered entries"; exit 1; }
	@echo "DECISIONS.md: ok"

ci: lint test semgrep gitleaks decisions
