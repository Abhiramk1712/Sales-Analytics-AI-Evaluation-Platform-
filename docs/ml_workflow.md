# ML Workflow

## Principles

- Keep training/inference concerns explicit.
- Return warnings when confidence is low or data is limited.
- Track model runs with metadata for monitoring.
- Prevent leakage in deal scoring features.

## Endpoints

Inference endpoints:
- `GET /ml/forecast/revenue`
- `GET /ml/forecast/lab`
- `GET /ml/score/deals`
- `GET /ml/cluster/reps`

Training-trigger endpoints (lightweight wrappers around current workflow):
- `POST /ml/train/forecasting`
- `POST /ml/train/deal-scoring`
- `POST /ml/train/rep-clustering`

Monitoring endpoint:
- `GET /ml/model-runs`

## Model Metadata

Each model run captures:
- `model_name`
- `model_version`
- `trained_at`
- `training_rows`
- `feature_names`
- `target`
- `metrics`
- `limitations`
- `artifact_path`
- `data_hash`

## Persistence

Model runs are persisted to DB table `model_runs` and also mirrored to local JSON (`backend/ml/saved/model_runs.json`) as a fallback path.

Prediction outputs are persisted to DB table `ml_predictions` for:
- revenue forecasting
- deal scoring
- rep clustering

## Leakage Prevention (Deal Scoring)

Excluded from features:
- terminal outcome labels and post-close fields
- explicit closed outcome values as predictive features

This keeps scoring grounded in pre-outcome/snapshot-style attributes.

## Confidence and Warnings

- Forecasting returns warnings for short history windows.
- Rep clustering labels explicit fallback use if avg sales cycle cannot be computed.
- Forecasting uses baseline low-confidence fallback when history is below full-model threshold.
- Forecasting metadata includes rolling-origin backtest summary when sufficient history exists.
