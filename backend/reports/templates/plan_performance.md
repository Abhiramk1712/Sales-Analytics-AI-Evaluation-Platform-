# Plan Performance Report — {{ plan.name }} | {{ period }}
_Generated: {{ generated_at }}_

{% if warnings %}
> **⚠️ Data Warnings**
{% for w in warnings %}- {{ w }}
{% endfor %}
{% endif %}

## Plan Overview

| Field | Value |
|-------|-------|
| Plan Name | **{{ plan.name }}** |
| Period | {{ period }} |
| Effective Start | {{ plan.effective_start_date or "—" }} |
| Effective End | {{ plan.effective_end_date or "Open" }} |

## Aggregate Attainment

| Metric | Value |
|--------|-------|
| Total Revenue | **${{ "{:,.0f}".format(metrics.total_revenue) }}** |
| Total Quota | **${{ "{:,.0f}".format(metrics.total_quota) }}** |
| Attainment | **{{ metrics.attainment_pct | round(1) }}%** |
| Reps on Plan | **{{ metrics.rep_count }}** |
| Reps at Quota | **{{ metrics.reps_at_quota }}** |

## Rules

| Rule | Metric | Tier Min | Tier Max | Rate | Bonus |
|------|--------|----------|----------|------|-------|
{% for rule in rules %}
| {{ rule.name }} | {{ rule.metric_name }} | {{ rule.threshold_min }}% | {{ rule.threshold_max }}% | {{ (rule.rate * 100) | round(2) }}% | ${{ "{:,.0f}".format(rule.bonus_amount or 0) }} |
{% endfor %}

## Rep Attainment Breakdown

| Rep | Revenue | Quota | Attainment | Payout |
|-----|---------|-------|------------|--------|
{% for rep in reps %}
| {{ rep.name }} | ${{ "{:,.0f}".format(rep.revenue) }} | ${{ "{:,.0f}".format(rep.quota) }} | {{ rep.attainment_pct | round(1) }}% | ${{ "{:,.0f}".format(rep.payout_amount or 0) }} |
{% endfor %}

## Distribution Analysis

- **Reps ≥ 100% attainment**: {{ metrics.reps_at_quota }}
- **Reps 75–99% attainment**: {{ metrics.reps_near_quota }}
- **Reps < 75% attainment**: {{ metrics.reps_below_quota }}

## Data Lineage
- Period: `{{ period }}`
- Source tables: plans, rules, quotas, revenue, payouts
- Confidence: {{ metrics.confidence }}
