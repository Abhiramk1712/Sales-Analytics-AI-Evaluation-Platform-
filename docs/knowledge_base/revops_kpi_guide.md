# RevOps KPI Reference Guide

## ARR Waterfall Components
An ARR Bridge (Waterfall) decomposes net ARR change into:
- **New Logo ARR**: Revenue from customers who did not exist at the start of the period
- **Expansion ARR**: Upsells, cross-sells, and upgrades from existing customers (positive)
- **Contraction ARR**: Downgrades, reduced seats, or tier decreases from existing customers (negative)
- **Churn ARR**: Revenue from customers who fully cancelled (negative)
- **Renewal ARR**: Recurring revenue from existing customers renewing at the same level

Formula: Net New ARR = New Logo + Expansion + Contraction + Churn

## NRR (Net Revenue Retention)
NRR = (MRR_start + Expansion - Contraction - Churn) / MRR_start × 100

Benchmarks:
- Enterprise SaaS: NRR ≥ 120% is excellent, 100–120% is healthy, < 100% is at-risk
- SMB SaaS: NRR ≥ 100% is healthy, 90–100% is watch zone, < 90% is critical
- NRR > 100% means the existing customer base is growing without any new logo acquisition

## GRR (Gross Revenue Retention)
GRR = (MRR_start - Contraction - Churn) / MRR_start × 100 (capped at 100%)

GRR excludes expansion — it measures pure retention health.
- Healthy SaaS GRR: ≥ 85%
- GRR < 80% indicates structural churn that cannot be covered by expansion

## Pipeline Coverage Benchmarks
- Raw (unweighted) pipeline coverage: 4× quarterly quota is the minimum safe level
- Weighted pipeline coverage (deals × close probability): 3× quarterly quota
- Coverage < 2×: severe risk — even perfect conversion will miss quota
- Coverage 2×–3×: watch zone — requires active pipeline generation
- Coverage > 4×: healthy — deal selectivity is appropriate

## Quota Setting Best Practice (RevOps Formula)
Q = historical_p70_monthly_revenue × 3 (quarter) × growth_factor × ramp_factor

Where:
- historical_p70: 70th percentile of rep's last 12 months monthly revenue
- growth_factor: typically 1.10–1.25 for high-growth SaaS
- ramp_factor: 0.25 at hire, 0.50 at month 2, 0.75 at month 4, 1.00 at month 6+

Never set quota to an arbitrary round number or last-year-plus-20%. Always anchor to revenue history.

## Commit / Best Case / Most Likely Forecast Submission
- **Commit**: Deals the rep is committing with high conviction to close in the current period
- **Best Case**: Upside deals that could close but are not certain — typically commit + 30%
- **Most Likely / Model**: System-generated forecast based on pipeline probability weighting
- **Target**: The quota for the period

A healthy forecast: Most Likely ≥ Commit, Commit ≥ 0.85 × Target
