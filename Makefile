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

# Mutation over the module a pull request touched. A whole-repository run is minutes for a signal
# nobody reads, so CI passes the paths explicitly.
MUTATE ?= bin/
mutation: venv
	@$(VENV)/bin/python -m mutmut run --paths-to-mutate=$(MUTATE) || true
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
