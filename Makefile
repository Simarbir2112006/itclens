PYTHON ?= python3
VENV := .venv
BIN := $(VENV)/bin

.PHONY: install lint format typecheck test check

install:
	test -d $(VENV) || $(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

lint:
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format:
	$(BIN)/ruff check --fix .
	$(BIN)/ruff format .

typecheck:
	$(BIN)/mypy src

test:
	$(BIN)/pytest

check: lint typecheck test
