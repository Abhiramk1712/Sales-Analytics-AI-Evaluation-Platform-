# AI Revenue Operations Intelligence Platform

A full-stack **Sales Analytics, Revenue Operations, Forecasting, and Compensation Intelligence** platform built with **FastAPI, PostgreSQL, React, dbt, and applied Machine Learning**.

This project demonstrates how sales, finance, and RevOps teams can move from disconnected spreadsheets and manual reporting into a governed analytics platform with KPI dashboards, payout intelligence, forecasting workflows, explainable ML, data quality checks, and AI-assisted analysis.

---

## Table of Contents

- [Project Overview](#project-overview)
- [Why This Project Matters](#why-this-project-matters)
- [Core Capabilities](#core-capabilities)
- [Architecture](#architecture)
- [Tech Stack](#tech-stack)
- [Repository Structure](#repository-structure)
- [Data Model and Demo Data](#data-model-and-demo-data)
- [Machine Learning and AI Components](#machine-learning-and-ai-components)
- [dbt Analytics Layer](#dbt-analytics-layer)
- [API Surface](#api-surface)
- [Local Setup](#local-setup)
- [Docker Setup](#docker-setup)
- [Testing and Quality Checks](#testing-and-quality-checks)
- [GitHub Upload Checklist](#github-upload-checklist)
- [Demo Walkthrough](#demo-walkthrough)
- [Future Improvements](#future-improvements)
- [Portfolio Positioning](#portfolio-positioning)

---

## Project Overview

The platform is designed as an enterprise-style RevOps analytics system for analyzing sales performance, quota attainment, pipeline health, compensation payouts, forecasting accuracy, and rep-level performance.

It combines four major areas:

1. **Revenue Analytics** — KPIs, revenue trends, pipeline stages, attainment, win/loss, ARR waterfall, and territory performance.
2. **Sales Compensation Intelligence** — plans, rules, sales credits, payout calculations, payout readiness, quota suggestions, fairness checks, SPIFs, clawbacks, and audit trails.
3. **Machine Learning and Forecasting** — revenue forecasting, deal scoring, rep clustering, deal slip prediction, churn risk, territory forecasting, model monitoring, and explainability.
4. **AI-Assisted RevOps Workflows** — natural-language analysis, report generation, RAG-based knowledge retrieval, evidence-backed agent responses, and workflow execution.

The goal is not just to show a dashboard. The goal is to show a realistic data product that connects **business logic, analytics engineering, ML workflows, and software engineering** into one project.

---

## Why This Project Matters

Sales and RevOps teams often depend on spreadsheets for quota tracking, commission validation, and performance reporting. As organizations grow, this creates several problems:

- Manual payout calculations become difficult to audit.
- Forecasting becomes inconsistent across teams.
- Sales leaders lack real-time visibility into pipeline and attainment.
- Finance teams need explainable payout and compensation logic.
- Data teams need governed definitions for revenue, quota, attainment, and payout metrics.

This project addresses those problems by creating a structured platform where data ingestion, quality checks, governed metrics, ML insights, and dashboards are connected through one backend and one user interface.

---

## Core Capabilities

### Executive and RevOps Analytics

- Revenue KPI dashboard
- Monthly revenue trends
- Open pipeline and weighted pipeline views
- Win rate and deal velocity analysis
- Quota attainment by rep, team, and period
- ARR waterfall reporting
- Territory and manager hierarchy views
- Plan governance and sales performance views

### Compensation and Payout Intelligence

- Payout calculation workflows
- Team payout summaries
- Rep payout statements
- Sales credit analysis
- Plan and rule assignment tracking
- Quota suggestion and quota fairness endpoints
- SPIF and clawback support
- Payout audit trail, approval, lock, and adjustment flows

### Data Quality and Ingestion

- Company-level data loading
- Intelligent ingestion workflows
- Manifest validation
- Dry-run loading support
- Data quality summaries and checks
- Canonical mapping and relationship validation

### ML and Forecasting

- Revenue forecast API
- Forecasting lab workflows
- Model comparison and leaderboard endpoints
- Deal scoring and global feature importance
- Deal-level explainability
- Deal slip prediction
- Rep clustering
- Churn risk forecasting
- Territory forecasting
- Forecast accuracy and drift monitoring

### AI Agent and RAG

- Agent chat endpoint
- Streaming chat endpoint
- Tool-based workflow execution
- Evidence-backed fallback responses
- Local knowledge base retrieval
- Markdown report generation
- RevOps question-answering workflows

---

## Architecture

```mermaid
flowchart TD
    A[React + Vite Frontend] --> B[FastAPI Backend]

    B --> C[Analytics Routers]
    B --> D[Payout and Compensation Routers]
    B --> E[ML and Forecasting Routers]
    B --> F[Agent and RAG Routers]
    B --> G[Ingestion and Data Quality Routers]

    C --> H[PostgreSQL + SQLAlchemy Models]
    D --> H
    E --> H
    G --> H

    H --> I[dbt Staging Models]
    I --> J[dbt Intermediate Models]
    J --> K[dbt Marts]

    E --> L[ML Modules]
    F --> M[Knowledge Base + Agent Tools]
    B --> N[Reports and Governance Docs]
```

### High-Level Flow

```text
CSV / Demo Company Data
        ↓
Ingestion + Manifest Validation
        ↓
Data Quality and Relationship Checks
        ↓
PostgreSQL Operational Tables
        ↓
FastAPI Services and Routers
        ↓
Analytics, Payouts, ML Forecasts, Agent, Reports
        ↓
React Dashboard and API Consumers
```

---

## Tech Stack

| Layer | Tools |
|---|---|
| Backend | FastAPI, SQLAlchemy Async, Pydantic |
| Database | PostgreSQL, Alembic |
| Frontend | React, Vite, Recharts |
| Analytics Engineering | dbt staging, intermediate, and mart models |
| Machine Learning | pandas, NumPy, scikit-learn, statsmodels, SciPy |
| AI / Agent Layer | LLM provider abstraction, OpenAI-compatible provider, RAG scaffold |
| Testing | pytest, pytest-asyncio |
| DevOps | Docker, docker-compose, GitHub Actions CI, Makefile |
| Documentation | Markdown architecture docs, model governance docs, data model docs |

---

## Repository Structure

```text
sales-analytics-ai/
├── backend/                  # FastAPI app, routers, services, ML, agent, auth, payout logic
│   ├── routers/              # API route groups
│   ├── ml/                   # Forecasting, scoring, clustering, monitoring, model registry
│   ├── payout/               # Compensation and payout calculation logic
│   ├── agent/                # Agent planner, executor, verifier, tools
│   ├── rag/                  # Local retrieval and knowledge base integration
│   ├── ingestion/            # Manifest loading, intelligent ingestion, source registry
│   ├── validation/           # Quality gates and RevOps validation rules
│   ├── metrics/              # Governed metric definitions and registry
│   ├── statistics/           # Descriptive stats, anomaly detection, funnel/driver analysis
│   └── services/             # Business service layer
│
├── frontend/                 # React dashboard application
│   └── src/
│       ├── pages/            # Dashboard pages: payouts, ARR waterfall, scorecards, ML insights
│       └── components/       # Shared UI components
│
├── dbt/                      # Analytics engineering layer
│   └── models/
│       ├── staging/          # Source-aligned staging models
│       ├── intermediate/     # Business transformations
│       └── marts/            # Executive and operational reporting models
│
├── database/                 # SQL schema artifacts
├── migrations/               # Alembic database migrations
├── sample_data/              # Public-safe synthetic demo data
├── companies/                # Demo company datasets used by local runs
├── docs/                     # Architecture, ML governance, RBAC, data lifecycle, glossary
├── scripts/                  # Packaging, hygiene, audit, and utility scripts
├── tests/                    # Automated backend, ML, ETL, agent, API, and data quality tests
├── docker-compose.yml        # Local full-stack environment
├── Dockerfile                # Backend container
├── Makefile                  # Common setup/run/test commands
└── requirements.txt          # Python dependencies
```

---

## Data Model and Demo Data

The project contains a realistic RevOps and compensation data model with entities such as:

- Accounts
- Opportunities and deals
- Bookings
- Revenue
- Churn events
- Reps and teams
- Managers and hierarchy
- Territories
- Products
- Quotas
- Plans
- Rules
- Plan assignments
- Sales units
- Sales credits
- Payouts
- Activities
- ARR waterfall entries

A public-safe synthetic dataset is available under:

```text
sample_data/techo_solutions_demo/
```

The repository also includes local demo company datasets under:

```text
companies/techo-solutions/
companies/insurex/
```

These datasets support dashboard testing, API responses, and local demo workflows.

---

## Machine Learning and AI Components

The backend includes ML modules for:

- Revenue forecasting
- Forecasting lab experiments
- Forecast model comparison
- Forecast backtesting
- Deal scoring
- Deal slip prediction
- Rep clustering
- Territory forecasting
- Churn forecasting
- Pipeline forecasting
- Forecast accuracy tracking
- Model cards
- Model registry
- Drift detection
- Explainability and feature importance

Relevant folders:

```text
backend/ml/
backend/features/
backend/statistics/
backend/reports/
```

The ML layer is designed to be explainable for business stakeholders, not just technically functional. This makes the project useful for interviews involving data science, analytics engineering, RevOps analytics, and business-facing ML.

---

## dbt Analytics Layer

The `dbt/` folder adds a lightweight analytics engineering layer for portfolio credibility.

### Staging Models

Examples:

```text
stg_accounts.sql
stg_bookings.sql
stg_deals.sql
stg_payouts.sql
stg_revenue.sql
stg_sales_credits.sql
stg_quotas.sql
```

### Intermediate Models

Examples:

```text
int_quota_attainment.sql
int_rep_month_performance.sql
int_team_quarter_attainment.sql
int_payout_quality_signals.sql
int_plan_rule_coverage.sql
int_revenue_forecast_baseline.sql
```

### Mart Models

Examples:

```text
mart_exec_kpis.sql
mart_rep_performance.sql
mart_pipeline_health.sql
mart_payout_readiness.sql
mart_payout_anomalies.sql
mart_forecast_vs_actual.sql
mart_comp_plan_effectiveness.sql
```

This structure shows the standard analytics engineering pattern:

```text
Raw source tables → staging → intermediate business logic → reporting marts
```

---

## API Surface

The backend exposes grouped FastAPI endpoints for the major product modules.

Representative route groups:

| Area | Example Endpoints |
|---|---|
| Health | `/health` |
| Analytics | `/analytics/kpis`, `/analytics/revenue/monthly`, `/analytics/reps/performance` |
| Pipeline | `/analytics/pipeline/stages`, `/analytics/deal-velocity`, `/analytics/win-loss` |
| ARR | `/analytics/arr-waterfall`, `/ml/forecast/arr-waterfall` |
| Payouts | `/payout/calculate`, `/payout/team-summary`, `/payout/statements/{rep_id}` |
| Payout Audit | `/payout-audit`, `/payout-audit/{payout_id}/trace` |
| Plans | `/plans`, `/plans/{plan_id}/rules`, `/plans/{plan_id}/performance` |
| ML | `/ml/forecast/revenue`, `/ml/score/deals`, `/ml/cluster/reps` |
| Forecasting Lab | `/ml/forecast/lab`, `/ml/forecast/compare-models`, `/ml/forecast/model-leaderboard` |
| Explainability | `/ml/explain/global-importance`, `/ml/explain/deal/{deal_id}` |
| Data Quality | `/data-quality/summary`, `/data-quality/checks` |
| Ingestion | `/ingestion/inspect`, `/ingestion/intelligent-load`, `/ingestion/manifest/validate` |
| Agent | `/agent/chat`, `/agent/chat/stream` |
| Reports | `/reports/types`, `/reports/generate`, `/reports/knowledge-base` |

When the backend is running locally, interactive API documentation is available at:

```text
http://localhost:8000/docs
```

---

## Local Setup

### Prerequisites

Install the following:

- Python 3.11+
- Node.js 18+
- PostgreSQL 15+
- Git

### 1. Clone the Repository

```bash
git clone <your-repository-url>
cd sales-analytics-ai
```

### 2. Create Environment File

```bash
cp .env.example .env
```

Update `.env` if needed:

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/sales_analytics
ENVIRONMENT=development
DEMO_MODE=true
DEMO_DEFAULT_ROLE=executive
DEMO_DEFAULT_COMPANY=techo-solutions
OPENAI_API_KEY=
ANTHROPIC_API_KEY=
LLM_PROVIDER=openai
```

The project can run in demo mode without real LLM credentials. If an LLM key is missing, the agent layer is designed to return fallback/evidence-backed responses where supported.

### 3. Install Dependencies

```bash
make setup
```

This creates a Python virtual environment, installs backend dependencies, and installs frontend dependencies.

### 4. Start Backend

```bash
make backend
```

Backend runs at:

```text
http://localhost:8000
```

### 5. Start Frontend

Open a second terminal:

```bash
make frontend
```

Frontend runs at the Vite local development URL, usually:

```text
http://localhost:5173
```

---

## Docker Setup

You can also run the project with Docker Compose:

```bash
docker compose up --build
```

This starts:

- PostgreSQL database on port `5432`
- FastAPI backend on port `8000`
- React frontend on port `3000`

Useful URLs:

```text
Backend API: http://localhost:8000
API Docs:    http://localhost:8000/docs
Frontend:    http://localhost:3000
```

---

## Testing and Quality Checks

### Run Backend Tests

```bash
make test
```

Equivalent command:

```bash
pytest -q
```

### Run Backend Compile Check and Frontend Build

```bash
make lint
```

### Run Package Hygiene Check

```bash
python3 scripts/check_package_hygiene.py --path .
```

### Create Clean Public Package

```bash
make package
```

or:

```bash
bash scripts/package_clean.sh
```

The clean package is generated under:

```text
dist/packages/
```

---

## GitHub Upload Checklist

Before uploading this project to GitHub, make sure the repository is clean and public-safe.

### Do Not Upload These Folders or Files

The inspected zip contained local/generated files that should not be committed:

```text
.venv/
venv/
node_modules/
frontend/node_modules/
dist/
frontend/dist/
__pycache__/
.pytest_cache/
.DS_Store
__MACOSX/
.env
*.pyc
*.pkl
*.joblib
```

### Cleanup Commands

Run these from the project root before committing:

```bash
rm -rf .venv venv node_modules frontend/node_modules dist frontend/dist
find . -type d -name "__pycache__" -prune -exec rm -rf {} +
find . -type f -name ".DS_Store" -delete
find . -type d -name "__MACOSX" -prune -exec rm -rf {} +
find . -type f -name "*.pyc" -delete
```

### Recommended GitHub Commit Flow

```bash
git init
git add .
git status
python3 scripts/check_package_hygiene.py --path .
git commit -m "Initial public portfolio release"
git branch -M main
git remote add origin <your-github-repo-url>
git push -u origin main
```

### Confirm Before Push

Check that these files exist:

```text
README.md
.env.example
.gitignore
requirements.txt
Dockerfile
docker-compose.yml
Makefile
backend/
frontend/
dbt/
docs/
tests/
sample_data/
```

Check that these files do **not** exist in Git status:

```text
.env
.venv/
frontend/node_modules/
frontend/dist/
__pycache__/
.DS_Store
__MACOSX/
```

---

## Demo Walkthrough

A strong demo flow for interviews or GitHub reviewers:

1. Start the backend and frontend.
2. Open the dashboard and explain revenue, pipeline, win rate, and quota attainment KPIs.
3. Show rep performance and leadership rollups.
4. Walk through payout summaries, payout statements, and payout audit trails.
5. Open ML insights and explain revenue forecasting, deal scoring, and model monitoring.
6. Show the dbt folder and explain staging → intermediate → mart modeling.
7. Open the API docs at `/docs` and explain the route structure.
8. Explain how the AI agent uses tools, RAG, and evidence-backed responses.
9. Finish by explaining how this project connects analytics engineering, ML, backend engineering, and RevOps business logic.

---

## Future Improvements

Planned improvements that would make the platform even closer to production quality:

- Add deployed demo links for backend and frontend.
- Add screenshots or GIFs under a GitHub `assets/` folder.
- Add JWT-based production authentication flow.
- Add stricter tenant isolation for production mode.
- Add dbt execution into CI with a seeded test database.
- Add model artifact versioning with a real model registry backend.
- Add pgvector or another vector database for improved RAG retrieval.
- Add more robust frontend error states and loading states.
- Add Power BI or Tableau export layer for BI analyst portfolio positioning.
- Add CRM ingestion adapters for Salesforce or HubSpot-style source data.

---

## Portfolio Positioning

This project can be positioned for multiple job tracks:

### Data Analyst / BI Analyst

- KPI dashboards
- Revenue and pipeline reporting
- Quota attainment analysis
- Business-facing metrics dictionary
- Executive-ready reporting layer

### Analytics Engineer

- dbt staging, intermediate, and mart models
- Source-to-mart lineage
- Data quality checks
- Governed metric definitions
- SQL transformation logic

### Data Scientist

- Forecasting workflows
- Deal scoring
- Rep clustering
- Drift detection
- Explainability and feature importance
- Model cards and model governance

### Backend / AI Engineer

- FastAPI service architecture
- Modular routers and services
- AI agent workflow
- RAG scaffold
- LLM provider abstraction
- Dockerized deployment foundation

### RevOps / Sales Compensation Analyst

- Plans, rules, quotas, credits, and payouts
- Payout auditability
- Quota fairness and quota suggestions
- Plan governance analytics
- Compensation data model

---

## Project Status

Current status: **Portfolio-ready after cleanup**

The project has strong backend depth, meaningful business logic, ML components, a React dashboard, dbt modeling assets, documentation, Docker support, CI configuration, and automated tests.

Before publishing publicly, clean local artifacts and make sure only source code, documentation, configuration examples, tests, and public-safe sample data are committed.

---

## Author

**Abhiram Kattunga**  
Graduate Data Science Student | AI / ML | RevOps Analytics | Full-Stack Data Products

