# Commands for every build step. Run `make` on its own to list them.

PY ?= python3
export PYTHONPATH := src
HOST ?= 127.0.0.1
PORT ?= 8080
BASE := http://$(HOST):$(PORT)

.DEFAULT_GOAL := help

help:  ## List available commands
	@grep -hE '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | awk -F':.*?## ' '{printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

install:  ## Install dependencies and the browser Playwright drives
	$(PY) -m pip install -r requirements.txt
	$(PY) -m playwright install chromium

run:  ## Start ShareBase, the target application
	$(PY) -m sharebase

test:  ## Run the whole test suite
	$(PY) -m pytest

test-hostile:  ## Only the target's hostile-surface property tests
	$(PY) -m pytest tests/sharebase/test_hostile_surface.py -v

test-flow:  ## Only the flow and business-outcome tests
	$(PY) -m pytest tests/sharebase/test_flow.py -v

test-faults:  ## Only the fault-taxonomy tests
	$(PY) -m pytest tests/sharebase/test_faults.py -v

test-surface:  ## Only the surface-layer tests (drives a real browser)
	$(PY) -m pytest tests/surface -v

test-contract:  ## Only the capability schema tests
	$(PY) -m pytest tests/contract -v

test-policy:  ## Only the guardrail tests (allowlist, risk, redaction)
	$(PY) -m pytest tests/policy -v

test-replay:  ## Only the replay tests (drives a real browser; ~3 min)
	$(PY) -m pytest tests/replay -v

test-discovery:  ## Only the discovery tests (scripted model, no API key)
	$(PY) -m pytest tests/explore -v

test-fast:  ## Everything except the browser tests (about a second)
	$(PY) -m pytest tests/sharebase tests/contract tests/policy \
		tests/surface/test_naming.py tests/surface/test_observation.py \
		tests/replay/test_resolve.py tests/explore/test_recorder.py

# Arm a fault against a running ShareBase, e.g. `make fault F=hard_error`.
fault:  ## Arm one fault (F=<name>) on a running ShareBase
	@test -n "$(F)" || (echo "usage: make fault F=hard_error"; exit 1)
	@curl -sS -X POST $(BASE)/admin/faults -H 'Content-Type: application/json' \
		-d '{"$(F)": true}' | $(PY) -m json.tool

faults:  ## Show the current fault state
	@curl -sS $(BASE)/healthz | $(PY) -m json.tool
	@echo "fault console: $(BASE)/admin/faults"

reset:  ## Disarm every fault and reseed member data
	@curl -sS -X POST $(BASE)/admin/reset -H 'Content-Type: application/json' \
		-d '{}' | $(PY) -m json.tool

observe:  ## Print what the surface layer sees on one page (URL=<path>)
	@$(PY) -m surface.cli $(URL)

capabilities:  ## Re-author the seed capability artifacts from the live app
	$(PY) tools/author_capabilities.py

review:  ## Print a capability for human review (CAP=<id@version>)
	@$(PY) -m contract.cli $(CAP)

# Replay a saved capability against a running ShareBase, e.g.
#   make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"
replay:  ## Replay a capability (CAP=<id@version> IN="--input k=v")
	@test -n "$(CAP)" || (echo 'usage: make replay CAP=member.savings_balance@1.0.0 IN="--input member_id=12345"'; exit 1)
	@$(PY) -m replay.cli $(CAP) $(IN)

# Record a new capability by letting a model drive the app. Needs
# OPENAI_API_KEY; add TRANSCRIPT=<path> to replay a recorded run instead.
discover:  ## Record a capability with a model (GOAL="..." ID=<id>)
	@test -n "$(GOAL)" || (echo 'usage: make discover GOAL="look up member 12345 and read their savings balance" ID=member.savings_balance'; exit 1)
	$(PY) -m explore.cli "$(GOAL)" --id $(or $(ID),recorded.capability) $(if $(TRANSCRIPT),--transcript $(TRANSCRIPT))

evidence:  ## Produce the evidence runs in evidence/
	$(PY) tools/make_evidence.py

clean:  ## Remove caches and build leftovers
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf .pytest_cache *.egg-info src/*.egg-info

.PHONY: help install run test test-hostile test-flow test-faults test-surface test-contract test-policy test-replay test-discovery test-fast fault faults reset clean observe capabilities review replay discover evidence
