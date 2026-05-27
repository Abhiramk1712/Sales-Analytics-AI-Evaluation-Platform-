# Revenue Forecast Summary — {{ period }}
_Generated: {{ generated_at }}_

**Audience:** {{ audience }}

## Forecast Model
- **Model Info:** {{ model_info }}
- **Confidence:** {{ confidence }}
- **History Length:** {{ history_months }} months

## Forecast Periods
{% if rows %}
| Period | Forecast | Lower CI | Upper CI |
|--------|----------|----------|----------|
{% for row in rows %}
| {{ row.period }} | ${{ "{:,.0f}".format(row.forecast) }} | ${{ "{:,.0f}".format(row.lower_ci) }} | ${{ "{:,.0f}".format(row.upper_ci) }} |
{% endfor %}
{% else %}
_No forecast periods available._
{% endif %}

## Model Metrics
- **MAE:** ${{ "{:,.0f}".format(metrics.MAE) if metrics.MAE is not none else "N/A" }}
- **RMSE:** ${{ "{:,.0f}".format(metrics.RMSE) if metrics.RMSE is not none else "N/A" }}
- **MAPE:** {{ (metrics.MAPE | round(2)) if metrics.MAPE is not none else "N/A" }}%

{% if warnings %}
## Warnings
{% for w in warnings %}
- {{ w }}
{% endfor %}
{% endif %}

## Data Lineage
- Sources: {{ sources | join(', ') if sources else 'unavailable' }}
