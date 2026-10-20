# Convenience shortcuts for the checks CI runs (.github/workflows/validate.yml).
#
# Targets:
#   make validate     - structural fixture validation (tools/validate/validate.py)
#   make badge        - regenerate README.md's fixture-count badge (tools/badge/badge.py)
#   make badge-check  - fail if that badge is stale (what CI runs)
#   make test         - repository test suite (unittest discover tests)
#   make check-links  - check fixture source reference links (tools/validate/check_source_links.py)
#   make coverage     - test suite under branch coverage of tools/, failing below
#                       .coveragerc's fail_under (what CI's coverage job runs);
#                       needs the dev dependency `pip install coverage==7.16.2`
#   make registry     - write PACK/registry-v1.json (tools/registry/generate.py);
#                       PACK defaults to protocol-28. Not part of `make check`.
#   make pack         - build dist/protocolcanary-fixtures-VERSION.tar.gz for PACK
#                       (tools/pack/pack.py); VERSION is required, e.g. VERSION=1.0.0
#   make check        - all of the above, in CI's order; stops at the first failure
#
# Run from the repository root (make defaults to this file's directory).
# Requires Python 3.11+ (the validator uses the stdlib `tomllib` module).

PACK ?= protocol-28

.PHONY: validate badge badge-check test check-links coverage registry pack check

validate:
	python3 tools/validate/validate.py

check-links:
	python3 tools/validate/check_source_links.py

badge:
	python3 tools/badge/badge.py

badge-check:
	python3 tools/badge/badge.py --check

test:
	python3 -m unittest discover tests -v

coverage:
	coverage run -m unittest discover tests
	coverage report -m

registry:
	python3 tools/registry/generate.py $(PACK)

pack:
	@test -n "$(VERSION)" || { echo "usage: make pack VERSION=1.0.0 [PACK=protocol-28]" >&2; exit 2; }
	python3 tools/pack/pack.py $(PACK) --version $(VERSION) --output-dir dist

check: validate badge-check test
