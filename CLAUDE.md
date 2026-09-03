# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

---

## Project

An AI-assisted sales compensation and RevOps analytics platform. FastAPI + PostgreSQL
backend, React/Vite frontend, dbt over the same warehouse, applied ML for forecasting
and deal scoring. It is a master's project and a reference implementation — synthetic
data, single deployment, no production traffic.

The load-bearing capability is **traceable commission**, not the dashboards. A closed
deal becomes a `SalesUnit`, split into `SalesCredit` rows (one per person credited, each
with a percentage), resolved against a `PlanAssignment` → versioned `Plan` → `Rule` rows
carrying tiers, rates, accelerators and bonuses, producing a `PayoutRecord` with a
`formula_trace` recording the derivation. Everything else supports that chain.

**Tenancy is query-scoped, and enforced at the session — not the call site.**
`backend/tenancy.py` carries the tenant in a `ContextVar` (bound per request by the
middleware in `main.py`), and `backend/tenant_guard.py` uses it to add
`WHERE company_id = ...` to every ORM select and to stamp `company_id` on every insert.
`company_id` is on 40 of 41 tables. Do not add filters by hand; they are already applied.
For deliberate cross-tenant work use `tenant_guard.unscoped()`, so a bypass is always a
visible decision. Writing a tenant row with no tenant bound raises rather than silently
creating a row no tenant can see.

Tenancy was originally a whole-database swap: loading one company dropped every table, so
the server held exactly one tenant at a time and a request naming another rebuilt the
database to serve it. That is gone — `drop_all` appears nowhere, and
`tests/test_tenancy_enforcement.py` holds two companies resident at once.

---

## Commands

Every command here was run against this repo before being written down. If one stops
working, fix the command or fix this file — do not leave a broken incantation in place.

```bash
make setup      # venv + pip install -r requirements.txt + npm install
make seed       # generate companies/techo-solutions and load it into the DB
make backend    # uvicorn on :8000
make frontend   # vite dev server on :3000 (NOT 5173 — vite.config.js sets 3000)
make test       # pytest -q, ~29s, 796 tests
make coverage   # pytest -q --cov=backend --cov-report=term-missing
make lint       # compileall backend + vite build
make package    # clean shareable zip
make clean      # drop caches and build output
```

```bash
# One test, one test method
.venv/bin/python -m pytest tests/test_payout_engine.py -q
.venv/bin/python -m pytest "tests/test_payout_attainment_grain.py::test_eight_credits_summed_cross_into_the_second_tier" -q

# Packaging hygiene — this gates CI, and it fails on a local .env by design
.venv/bin/python scripts/check_package_hygiene.py --path .

# Migrations. env.py overrides the URL from DATABASE_URL.
.venv/bin/alembic heads
.venv/bin/alembic upgrade head

# What the attainment-grain defect cost, computed from a company's own CSVs
.venv/bin/python -m scripts.analyze_attainment_grain_impact --company techo-solutions
```

`make seed` is **destructive**: it regenerates `companies/<name>/` and then drops and
recreates every table to load it. Do not run it against a database whose contents matter.

### Local addresses

| Service | Address |
| --- | --- |
| Backend | `http://localhost:8000` (`/docs` for OpenAPI) |
| Frontend | `http://localhost:3000` — proxies API prefixes to `127.0.0.1:8000` |
| PostgreSQL | `localhost:5432`, `postgres/postgres`, db `sales_analytics` |

---

## Architecture notes that grep won't give you

- **`backend/company_context.py` holds process-global state.** `_active_company` is shared
  by every concurrent request. It is being replaced by `backend/tenancy.py`; prefer the
  `ContextVar` for anything new.
- **Two schema sources can disagree.** `backend/models.py` (used by `create_all`),
  `database/schema.sql`, and `migrations/versions/`. `AUTO_CREATE_TABLES` defaults true,
  so in practice `create_all` builds the schema and Alembic never runs — meaning drift
  between the three is invisible until something reads the wrong one.
  `tests/test_schema_consistency.py` and `tests/test_tenancy_foundation.py` guard this.
- **The payout engine has two paths.** `credit_payout_engine.py` uses `SalesCredit` rows
  when they exist and falls back to rep-level revenue aggregation when they do not. Both
  must stay consistent; they have diverged before (see CORR-1 below).
- **Demo mode and production mode are different auth systems**, not one system with a
  flag. Demo mode reads identity from headers so the persona switcher works. Production
  mode reads a verified JWT and ignores identity headers entirely. See
  `backend/auth/dependencies.py` — the two paths are deliberately kept visibly separate.

---

## Rules, and the incident behind each one

Every rule here exists because something went wrong in this repository. None are
speculative. If a rule stops matching reality, delete it.

### Money paths — agree the approach before the first edit

```text
backend/payout/**
backend/metrics/**
backend/audit/payout_audit.py
```

State what you intend to change and why, in a sentence or two, before editing. Answer one
question out loud first: **at what grain does this calculation apply?**

*Why:* commission tiers were evaluated per `SalesCredit` instead of per period. Attainment
is cumulative — the eighth deal is paid at a higher rate *because of* the seven before it.
Judged one credit at a time, tiers never fired for anyone with more than one credit, flat
tier bonuses were paid once per credit instead of once per quarter, and attainment
inflated past the top tier's ceiling so no band matched and the payout computed to zero.
On the reference data that misallocated **$1,205,000 across 152 of 189 rep-quarters**,
including 20 paid $0 while owed money. The bug is one line of arithmetic at the wrong
grain, and stating the grain beforehand costs nothing.

**This is enforced by a hook**, not by memory of this file — see `.claude/settings.json`.
Record the approach once per branch and it stops asking:

```bash
mkdir -p .claude/plan-state
echo "<the agreed approach, one line>" \
  > ".claude/plan-state/$(git rev-parse --abbrev-ref HEAD | tr '/' '_').txt"
```

Test files are deliberately not gated — writing the test first is the behaviour this is
trying to produce.

### Destructive database operations

Loading a company replaces only that company's rows (`_delete_company_rows`). Nothing may
reintroduce a whole-database drop, and any new bulk-delete path must be scoped by
`company_id` and require the `ALLOW_DESTRUCTIVE_LOAD` flag.

*Why:* `load_company_dataset` used to call `Base.metadata.drop_all`, and the middleware
invoked it whenever a request named a different company than the process-global active
one. `GET /analytics/kpis?company_id=other` therefore dropped and rebuilt the entire
database, unauthenticated, and the project's own `ALLOW_DESTRUCTIVE_LOAD` guard was never
consulted on that path.

### Uniqueness on natural keys must include company_id

Two tenants legitimately share human-readable identifiers — every generated dataset
numbers positions `POS-001` upward.

*Why:* those columns carried global `UNIQUE` constraints, which was invisible while one
company was resident at a time and became a hard failure the moment two could be: loading
the second collided on the first's rows. UUID-keyed uniques are fine as they are.

### Verify commands before writing them down

Do not transcribe a command from the README, from memory, or from this file without
running it.

*Why:* the README's setup instructions told you to run `alembic upgrade head`, and
`alembic.ini` had a duplicate `sqlalchemy.url` key that made **every** alembic command
fail with `DuplicateOptionError`. It had presumably never been run. The README also
documented the frontend on port 5173 while `vite.config.js` sets 3000.

### A green check is not evidence unless something proves it measured

*Why:* `tests/test_payout_audit.py` parametrizes over whatever is in `companies/`. That
directory was untracked, so on a fresh clone the list was empty and every test in the file
passed while checking nothing. The module now refuses to run on an empty list. Apply the
same suspicion to any check whose scope is discovered at runtime.

### Confirm before deleting reachable functionality

Before removing a page, component, route or endpoint, check: is it routed or imported? Is
there a backend counterpart? Was this specific path actually named in the task? Does the
stated justification still match what the code does?

*Why:* `IngestionTab` was 285 lines of working UI that nothing routed to — easily
mistaken for dead code. It was the only interface to the manifest-driven ingestion layer.
It got re-routed rather than deleted, because someone asked first.

### Test isolation

Tests that reload `backend.config` or patch `DATABASE_URL` must restore both the module
and `backend.database._engine` on teardown.

*Why:* `test_destructive_load_guard`'s fixture put the env back but left the placeholder
URL in the reloaded settings object and the cached engine, so every later test in the
session inherited a database that does not exist. The suite passed or failed depending on
file order.

---

## Test coverage — what is actually enforced

| | Enforced? |
| --- | --- |
| Backend tests pass | **Yes** — `pytest -q` in CI, blocking. 796 tests. |
| Packaging hygiene | **Yes** — `check_package_hygiene.py` in CI, blocking. |
| Frontend build | **Yes** — `npm run build` in CI, blocking. |
| Backend coverage % | **No threshold, no CI gate.** `make coverage` measures it locally (58% of `backend/` by line, last measured); nothing fails a build over it. |
| Frontend tests | **No.** There is no test runner installed. |
| Frontend lint / types | **No.** No ESLint, no TypeScript. |
| dbt tests | **No.** `schema.yml` exists; `dbt test` never runs in CI. |

Four of seven rows are honest gaps. Do not describe this project as having test coverage
enforcement — it has a passing test suite, which is a different claim. If you add a
threshold, measure the baseline first and raise it to that, rather than asserting a
number ahead of the measurement.

---

## Claims about the environment need a command

Before stating that a tool, service or endpoint does or does not work, run it and show the
output. This cuts both ways — a confident unverified negative wastes as much time as a
confident unverified positive. A directory listing or a version string is evidence *about*
the thing, not the thing.

Verified present: Python 3.12, Node 26, npm 11, PostgreSQL on :5432, `gh`. Docker is
**not** installed on this machine despite `docker-compose.yml` existing — `make setup`
and a local Postgres are the working path.

---

## Keeping this file honest

`scripts/check_claude_md.py` holds this file's checkable claims against the repo —
make targets, the frontend port, the tenant table counts, what CI actually enforces,
whether alembic runs, and the test count. It runs at session start in report mode via
`.claude/settings.json`, and stays silent unless something drifted.

```bash
.venv/bin/python scripts/check_claude_md.py             # full report
.venv/bin/python scripts/check_claude_md.py --skip-slow # skip test collection
```

It exists because this repository has already shipped three documented facts that were
false: a setup step calling a command that crashed on every invocation, a frontend port
that did not match the config, and a schema file two tables behind the ORM. Prose and
rationale are not checkable and are not checked — when a rule here stops matching
reality, delete it rather than working around it.

## Response style

Terse. Lead with the result; no preamble, no trailing summary. Expand when asked why or
how. In code, write comments where they help a future reader and keep task references
(`# fixes #123`) in the commit message instead.
