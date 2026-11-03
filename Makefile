# NEXUS developer targets. Python is the standard library only for these targets.
PYTHON ?= python3

.PHONY: index lint-runbooks

# Spec section 15, phase 3: lint, ingest, index. Writes build/index/{index,manifest}.json.
index:
	$(PYTHON) -m experiments.harness.rag index --out build/index runbooks

lint-runbooks:
	$(PYTHON) -m experiments.harness.rag lint runbooks
