# Convenience shortcuts for the checks CI runs (.github/workflows/validate.yml).
#
# Targets:
#   make validate     - structural fixture validation (tools/validate/validate.py)
#   make badge        - regenerate README.md's fixture-count badge (tools/badge/badge.py)
#   make badge-check  - fail if that badge is stale (what CI runs)
#   make test         - repository test suite (unittest discover tests)
#   make registry     - write PACK/registry-v1.json (tools/registry/generate.py);
#                       PACK defaults to protocol-28. Not part of `make check`.
#   make check        - all of the above, in CI's order; stops at the first failure
#
# Run from the repository root (make defaults to this file's directory).
# Requires Python 3.11+ (the validator uses the stdlib `tomllib` module).

PACK ?= protocol-28

.PHONY: validate badge badge-check test registry check

validate:
	python3 tools/validate/validate.py

badge:
	python3 tools/badge/badge.py

badge-check:
	python3 tools/badge/badge.py --check

test:
	python3 -m unittest discover tests -v

registry:
	python3 tools/registry/generate.py $(PACK)

check: validate badge-check test
