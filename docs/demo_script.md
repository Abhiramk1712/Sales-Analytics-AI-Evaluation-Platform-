# Demo Script

This script is designed for a 10-15 minute public portfolio walkthrough.

## 1. Startup

```bash
cp .env.example .env
make setup
make backend
make frontend
```

Open:

- Backend docs: http://localhost:8000/docs
- Frontend app: http://localhost:3000

## 2. KPI and Pipeline Story

- Show dashboard KPIs and period filters.
- Show pipeline stage distribution and weighted pipeline behavior.
- Explain how revenue and quota metrics align with payout context.

## 3. Forecast and ML Story

- Open forecasting pages and revenue forecast views.
- Explain baseline forecasting plus model workflow positioning.
- Show model-related routes from API docs if needed.

## 4. Compensation Story

- Walk through plans, rules, assignments, and payout routes.
- Explain payout readiness and anomaly concepts from mart models.

## 5. Data Quality and Reporting Story

- Show data-quality route outputs.
- Generate one report to demonstrate end-to-end workflow.

## 6. Public Packaging Story

- Open `sample_data/techo_solutions_demo/` and explain public-safe synthetic data.
- Open `dbt/models/` and explain staging -> intermediate -> marts lineage.
- Run hygiene check:

```bash
python3 scripts/check_package_hygiene.py --path .
```

## 7. Optional: Package Creation

```bash
bash scripts/package_clean.sh
```

Show resulting zip under `dist/packages/`.
