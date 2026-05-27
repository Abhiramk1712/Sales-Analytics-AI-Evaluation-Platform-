# Territory Performance Report — {{ territory.name }} | {{ period }}
_Generated: {{ generated_at }}_

{% if warnings %}
> **⚠️ Data Warnings**
{% for w in warnings %}- {{ w }}
{% endfor %}
{% endif %}

## Territory Overview

| Field | Value |
|-------|-------|
| Territory | **{{ territory.name }}** |
| Region | {{ territory.region or "—" }} |
| Segment | {{ territory.segment or "—" }} |
| Period | {{ period }} |

## Revenue Summary

| Metric | Value |
|--------|-------|
| Total Revenue | **${{ "{:,.0f}".format(metrics.total_revenue) }}** |
| Deals Closed (Won) | **{{ metrics.deals_won }}** |
| Win Rate | **{{ metrics.win_rate | round(1) }}%** |
| Avg Deal Size | **${{ "{:,.0f}".format(metrics.avg_deal_size) }}** |
| Open Pipeline | **${{ "{:,.0f}".format(metrics.open_pipeline) }}** |

## Rep Breakdown

| Rep | Revenue | Deals Won | Attainment |
|-----|---------|-----------|------------|
{% for rep in reps %}
| {{ rep.name }} | ${{ "{:,.0f}".format(rep.revenue) }} | {{ rep.deals_won }} | {{ rep.attainment_pct | round(1) }}% |
{% endfor %}

## Pipeline Hygiene

| Check | Count |
|-------|-------|
| Overdue Deals | {{ hygiene.overdue_count }} |
| Missing Close Date | {{ hygiene.missing_close_date_count }} |
| High-Prob Early Stage | {{ hygiene.high_prob_early_stage_count }} |

## Sub-Territories

{% if sub_territories %}
| Name | Revenue | Deals |
|------|---------|-------|
{% for sub in sub_territories %}
| {{ sub.name }} | ${{ "{:,.0f}".format(sub.revenue) }} | {{ sub.deals_won }} |
{% endfor %}
{% else %}
_No sub-territories defined._
{% endif %}

## Data Lineage
- Period: `{{ period }}`
- Source tables: territories, user_territory_assignments, revenue, deals
- Confidence: {{ metrics.confidence }}
