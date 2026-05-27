# RevOps Risk Report — {{ period }}
_Generated: {{ generated_at }}_

- Company: {{ company }}
- Data Freshness: {{ data_freshness }}

## KPI Snapshot

- Total Revenue: ${{ "{:,.0f}".format(summary.total_revenue) }}
- Quota Attainment: {{ summary.quota_attainment | round(2) }}%
- Open Pipeline: ${{ "{:,.0f}".format(summary.open_pipeline) }}

## Pipeline and Coverage

- Open Pipeline (calculator): ${{ "{:,.0f}".format(summary.pipeline_open) }}
- Weighted Coverage Ratio: {{ summary.weighted_coverage_ratio | round(2) }}x

## At-Risk Reps

| Rep | Revenue | Quota | Attainment |
|---|---:|---:|---:|
{% for rep in underperformers %}
| {{ rep.name }} | ${{ "{:,.0f}".format(rep.revenue) }} | ${{ "{:,.0f}".format(rep.quota) }} | {{ rep.attainment_pct | round(1) }}% |
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
