# ML Governance

## Model Run Metadata

The platform exposes model run records with governance fields:

- model_run_id
- company_id
- model_name / model_version
- task_type
- dataset_hash
- algorithm
- metrics_json
- confidence_label
- drift_status
- approved_for_demo / approved_for_production

API endpoints:

- GET /ml/model-runs
- GET /ml/model-monitoring/summary
- GET /ml/model-cards
- GET /ml/model-cards/{model_name}

## Model Cards

Model cards are included for:

- revenue_forecast
- deal_scoring
- rep_clustering
- churn_retention (scaffold)
- deal_slip

Each model card includes purpose, input data, included/excluded features,
validation method, metrics, limitations, recommended usage, and risk warnings.

## Governance Policy (Demo)

- Demo approvals default to approved_for_demo=true.
- Production approvals are explicit and remain false by default.
- Any low-confidence or high-error warning should trigger human review.

## Guardrails

- Forecast outputs include confidence labels and warnings.
- Deal scoring avoids target leakage by excluding post-outcome signals.
- Training endpoints can be blocked by critical data quality issues.
