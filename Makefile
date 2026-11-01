PYTHON ?= python3
.PHONY: check test
check: test
	$(PYTHON) -B scripts/check_package.py
test:
	$(PYTHON) -B -m unittest discover -s tests -v
