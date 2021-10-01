.PHONY: format format-check pylint typecheck lint test image
PYTHON := python

all: format lint test

format:
	$(PYTHON) -m black .

format-check:
	$(PYTHON) -m black --check .

pylint:
	$(PYTHON) -m pylint restfileserver tests

typecheck:
	$(PYTHON) -m mypy restfileserver

lint: format-check pylint typecheck

test:
	$(PYTHON) -m unittest discover -v tests/

image:
	docker build -t restfileserver .
