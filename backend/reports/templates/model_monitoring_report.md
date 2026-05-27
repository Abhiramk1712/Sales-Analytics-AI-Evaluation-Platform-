# Model Monitoring Report — {{ period }}
_Generated: {{ generated_at }}_

- Company: {{ company }}
- Data Freshness: {{ data_freshness }}

## Model Health Snapshot

| Model | Version | Last Trained | Key Metrics | Warnings |
|---|---|---|---|---|
{% for model in models %}
| {{ model.model_name }} | {{ model.model_version }} | {{ model.trained_at }} | {{ model.metrics }} | {{ model.warnings | join('; ') }} |
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
