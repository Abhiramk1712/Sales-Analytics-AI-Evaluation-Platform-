# Payout Accuracy and Audit Report — {{ period }}
_Generated: {{ generated_at }}_

- Company: {{ company }}
- Data Freshness: {{ data_freshness }}
- Total Payout: ${{ "{:,.2f}".format(total_payout) }}
- Rep Count: {{ rep_count }}
- Fallback Count: {{ fallback_count }}

## Sample Payout Rows

| Rep ID | Period | Credited Amount | Quota | Attainment | Final Payout | Fallback |
|---|---|---:|---:|---:|---:|---|
{% for row in rows %}
| {{ row.rep_id }} | {{ row.period }} | ${{ "{:,.2f}".format(row.credited_amount) }} | ${{ "{:,.2f}".format(row.quota) }} | {{ row.attainment | round(2) }} | ${{ "{:,.2f}".format(row.final_payout) }} | {{ row.fallback_mode }} |
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
