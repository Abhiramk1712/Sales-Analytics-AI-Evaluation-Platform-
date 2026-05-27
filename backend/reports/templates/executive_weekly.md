# Executive Weekly Report — {{ period }}
_Generated: {{ generated_at }}_

{% if warnings %}
> **⚠️ Data Warnings**
{% for w in warnings %}
> - {{ w }}
{% endfor %}
{% endif %}

## Summary

| Metric | Value | vs Prior Period |
|--------|-------|-----------------|
| Total Revenue | **${{ "{:,.0f}".format(metrics.total_revenue) }}** | {{ metrics.revenue_delta_pct | round(1) }}% |
| Quota Attainment | **{{ metrics.attainment_pct | round(1) }}%** | — |
| Win Rate | **{{ metrics.win_rate | round(1) }}%** | — |
| Open Pipeline | **${{ "{:,.0f}".format(metrics.open_pipeline) }}** | — |
| Pipeline Coverage | **{{ metrics.pipeline_coverage | round(2) }}×** | — |

## Top Performers This Period

{% for rep in top_reps[:5] %}
{{ loop.index }}. **{{ rep.name }}** — ${{ "{:,.0f}".format(rep.revenue) }} ({{ rep.attainment_pct | round(1) }}% attainment)
{% endfor %}

## Reps Needing Attention

{% set underperformers = reps | selectattr('attainment_pct', 'lt', 75) | list %}
{% if underperformers %}
| Rep | Attainment | Revenue | Quota |
|-----|------------|---------|-------|
{% for rep in underperformers[:10] %}
| {{ rep.name }} | {{ rep.attainment_pct | round(1) }}% | ${{ "{:,.0f}".format(rep.revenue) }} | ${{ "{:,.0f}".format(rep.quota) }} |
{% endfor %}
{% else %}
_All reps are at or above 75% attainment._
{% endif %}

## Pipeline Hygiene

- **Overdue deals** (past expected close): {{ hygiene.overdue_count }}
- **Missing close date**: {{ hygiene.missing_close_date_count }}
- **High-prob in early stage**: {{ hygiene.high_prob_early_stage_count }}

## Forecast (Next 3 Months)

{% if forecast %}
| Period | Base | Optimistic | Conservative |
|--------|------|------------|---------------|
{% for i in range(forecast.base.periods | length) %}
| {{ forecast.base.periods[i] }} | ${{ "{:,.0f}".format(forecast.base.values[i]) }} | ${{ "{:,.0f}".format(forecast.optimistic.values[i]) }} | ${{ "{:,.0f}".format(forecast.conservative.values[i]) }} |
{% endfor %}
_Strategy: {{ forecast.base.strategy_used }} | Backtest MAPE: {{ (forecast.base.backtest_mape * 100) | round(1) if forecast.base.backtest_mape else 'N/A' }}%_
{% endif %}

## Data Lineage

- Period: `{{ period }}`
- Source tables: {{ metrics.source_tables | join(', ') }}
- Confidence: {{ metrics.confidence }}
- Fallback mode: {{ metrics.fallback_mode }}

