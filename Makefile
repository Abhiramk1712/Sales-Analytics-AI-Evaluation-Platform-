SHELL := /bin/bash

PYTHON ?= python3
VENV ?= .venv
VENV_BIN := $(VENV)/bin

.PHONY: setup seed backend frontend test lint package clean

COMPANY ?= techo-solutions

setup:
	@if [[ ! -d "$(VENV)" ]]; then $(PYTHON) -m venv "$(VENV)"; fi
	"$(VENV_BIN)/python" -m pip install --upgrade pip
	"$(VENV_BIN)/python" -m pip install -r requirements.txt
	cd frontend && npm install

seed:
	"$(VENV_BIN)/python" -m backend.data_generator \
		--company-name "$(COMPANY)" \
		--n-reps 12 --n-accounts 60 --n-deals 150 --months 18 \
		--include-org-hierarchy

backend:
	"$(VENV_BIN)/uvicorn" backend.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	"$(VENV_BIN)/pytest" -q

lint:
	"$(VENV_BIN)/python" -m compileall backend
	cd frontend && npm run build

package:
	bash scripts/package_clean.sh

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/dist frontend/.vite dist/packages
