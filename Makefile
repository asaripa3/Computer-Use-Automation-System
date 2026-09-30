# Commands for every build step. Run `make` on its own to list them.

PY ?= python3
export PYTHONPATH := src
HOST ?= 127.0.0.1
PORT ?= 8080
BASE := http://$(HOST):$(PORT)

.DEFAULT_GOAL := help

help:  ## List available commands
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install runtime and test dependencies
	$(PY) -m pip install -r requirements.txt

run:  ## Start ShareBase, the target application
	$(PY) -m sharebase

test:  ## Run the whole test suite
	$(PY) -m pytest

test-surface:  ## Only the hostile-surface property tests
	$(PY) -m pytest tests/sharebase/test_surface.py -v

test-flow:  ## Only the flow and business-outcome tests
	$(PY) -m pytest tests/sharebase/test_flow.py -v

test-faults:  ## Only the fault-taxonomy tests
	$(PY) -m pytest tests/sharebase/test_faults.py -v

# Arm a fault against a running ShareBase, e.g. `make fault F=hard_error`.
fault:  ## Arm one fault (F=<name>) on a running ShareBase
	@test -n "$(F)" || (echo "usage: make fault F=hard_error"; exit 1)
	curl -sS -X POST $(BASE)/admin/faults -H 'Content-Type: application/json' \
	@-d '{"$(F)": true}' | $(PY) -m json.tool

faults:  ## Show the current fault state
	curl -sS $(BASE)/healthz | $(PY) -m json.tool
	@echo "fault console: $(BASE)/admin/faults"

reset:  ## Disarm every fault and reseed member data
	curl -sS -X POST $(BASE)/admin/reset -H 'Content-Type: application/json' \
	@-d '{}' | $(PY) -m json.tool

clean:  ## Remove caches and build leftovers
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache *.egg-info src/*.egg-info

.PHONY: help install run test test-surface test-flow test-faults fault faults reset clean
