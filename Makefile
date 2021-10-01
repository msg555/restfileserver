.PHONY: format format-check pylint typecheck lint test
PYTHON := python

all: format lint test

format:
	$(PYTHON) -m black .

format-check:
	$(PYTHON) -m black --check .

pylint:
	$(PYTHON) -m pylint weaverest tests

typecheck:
	$(PYTHON) -m mypy weaverest

lint: format-check pylint typecheck

test:
	$(PYTHON) -m unittest discover -v tests/
