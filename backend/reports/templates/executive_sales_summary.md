# Executive Sales Performance Summary — {{ period }}
_Generated: {{ generated_at }}_

- Company: {{ company }}
- Data Freshness: {{ data_freshness }}

## Key KPIs

| KPI | Value |
|---|---|
| Total Revenue | ${{ "{:,.0f}".format(key_metrics.total_revenue) }} |
| Total Quota | ${{ "{:,.0f}".format(key_metrics.total_quota) }} |
| Quota Attainment | {{ key_metrics.quota_attainment | round(1) }}% |
| Open Pipeline | ${{ "{:,.0f}".format(key_metrics.open_pipeline) }} |
| Win Rate | {{ key_metrics.win_rate | round(1) }}% |

## Top Reps

{% for rep in top_reps %}
- {{ rep.name }} — ${{ "{:,.0f}".format(rep.revenue) }} ({{ rep.attainment_pct | round(1) }}% attainment)
{% endfor %}

## Risks
{% for risk in risks %}
- {{ risk }}
{% endfor %}

## Recommendations
{% for recommendation in recommendations %}
- {{ recommendation }}
{% endfor %}

## Assumptions
{% for assumption in assumptions %}
- {{ assumption }}
{% endfor %}

{% if warnings %}
## Warnings
{% for warning in warnings %}
- {{ warning }}
{% endfor %}
{% endif %}
