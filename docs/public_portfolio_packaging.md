# Public Portfolio Packaging Guide

This document defines what is allowed in the public repository and clean handoff package.

## Allowed

- Application source code (`backend/`, `frontend/src/`, etc.)
- Public-safe docs and architecture notes
- Public-safe synthetic demo data under `sample_data/`
- dbt modeling assets under `dbt/`

## Disallowed

- `.env` and private credential files
- `.venv/`, `venv/`, `node_modules/`
- `frontend/dist/`, caches, temporary logs
- Runtime-generated company output folders
- Local ML binary artifacts unless explicitly approved

## Validation Command

```bash
python3 scripts/check_package_hygiene.py --path .
```

Expected result: no hygiene violations.

## Packaging Command

```bash
bash scripts/package_clean.sh
```

Expected result:

- Creates a staged clean directory and zip package in `dist/packages/`
- Excludes local/runtime/private artifacts
- Keeps only public-safe project assets

## Reviewer Checklist

- No secret or environment files in repository root.
- No virtualenv/node_modules/build artifacts committed.
- `sample_data/` exists and is small and synthetic.
- `dbt/` exists with sources, staging, intermediate, marts, and tests.
- README clearly positions project for public portfolio review.
