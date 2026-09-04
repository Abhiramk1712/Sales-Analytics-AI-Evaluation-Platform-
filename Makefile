SHELL := /bin/bash

PYTHON ?= python3
VENV ?= .venv
VENV_BIN := $(VENV)/bin

.PHONY: setup seed backend frontend test coverage lint package clean dbt-test

COMPANY ?= techo-solutions

setup:
	@if [[ ! -d "$(VENV)" ]]; then $(PYTHON) -m venv "$(VENV)"; fi
	"$(VENV_BIN)/python" -m pip install --upgrade pip
	"$(VENV_BIN)/python" -m pip install -r requirements.txt
	cd frontend && npm install

seed:
	"$(VENV_BIN)/python" -m backend.data_generator \
		--company-name "$(COMPANY)" \
		--n-reps 12 --n-accounts 60 --n-deals 150 --months 36 \
		--include-org-hierarchy

backend:
	"$(VENV_BIN)/uvicorn" backend.main:app --reload --port 8000

frontend:
	cd frontend && npm run dev

test:
	"$(VENV_BIN)/pytest" -q

coverage:
	"$(VENV_BIN)/pytest" -q --cov=backend --cov-report=term-missing

lint:
	"$(VENV_BIN)/python" -m compileall backend
	cd frontend && npm run build

dbt-test:
	@if [[ ! -f dbt/profiles.yml ]]; then cp dbt/profiles.example.yml dbt/profiles.yml; fi
	"$(VENV_BIN)/dbt" build --project-dir dbt --profiles-dir dbt

package:
	bash scripts/package_clean.sh

clean:
	find . -type d -name "__pycache__" -prune -exec rm -rf {} +
	rm -rf .pytest_cache .mypy_cache .ruff_cache frontend/dist frontend/.vite dist/packages
