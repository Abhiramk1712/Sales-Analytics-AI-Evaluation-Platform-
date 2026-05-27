# Forecast Confidence Report — {{ period }}
_Generated: {{ generated_at }}_

- Company: {{ company }}
- Data Freshness: {{ data_freshness }}

## Validation Metrics

| Metric | Value |
|---|---|
| MAE | {{ metrics.MAE if metrics.MAE is not none else "N/A" }} |
| RMSE | {{ metrics.RMSE if metrics.RMSE is not none else "N/A" }} |
| MAPE | {{ (metrics.MAPE * 100) | round(2) if metrics.MAPE is not none else "N/A" }}% |

## Forecast Output

{% if forecast.forecast_periods %}
| Period | Forecast | Lower CI | Upper CI |
|---|---:|---:|---:|
{% for i in range(forecast.forecast_periods | length) %}
| {{ forecast.forecast_periods[i] }} | ${{ "{:,.2f}".format(forecast.forecast_values[i]) }} | ${{ "{:,.2f}".format(forecast.lower_ci[i]) }} | ${{ "{:,.2f}".format(forecast.upper_ci[i]) }} |
{% endfor %}
{% else %}
No forecast output was available.
{% endif %}

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
