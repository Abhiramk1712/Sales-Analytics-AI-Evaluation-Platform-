# Data Quality Report — {{ period }}
_Generated: {{ generated_at }}_

- Company: {{ company }}
- Data Freshness: {{ data_freshness }}

## Severity Summary

- Critical: {{ critical | length }}
- Error: {{ error | length }}
- Warning: {{ warning | length }}

## Detailed Checks

| Check | Severity | Affected Entity | Affected Rows | Message | Remediation |
|---|---|---|---:|---|---|
{% for check in checks %}
| {{ check.name }} | {{ check.severity }} | {{ check.affected_entity }} | {{ check.affected_rows }} | {{ check.message }} | {{ check.remediation }} |
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
