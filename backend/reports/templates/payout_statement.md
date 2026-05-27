# Payout Statement — {{ period }}
_Generated: {{ generated_at }}_

**Audience:** {{ audience }}

## Summary
- **Total Payout:** ${{ "{:,.2f}".format(total_payout) }}
- **Rep Count:** {{ rep_count }}
- **Fallback Payouts:** {{ fallback_count }}

## Rep Payout Breakdown
{% if rows %}
| Rep ID | Period | Credited | Quota | Attainment | Commission | Final Payout | Mode |
|--------|--------|----------|-------|------------|------------|--------------|------|
{% for row in rows %}
| {{ row.rep_id_short }} | {{ row.period }} | ${{ "{:,.0f}".format(row.credited_amount) }} | ${{ "{:,.0f}".format(row.quota) }} | {{ row.attainment | round(1) }}% | ${{ "{:,.0f}".format(row.base_commission) }} | ${{ "{:,.0f}".format(row.final_payout) }} | {{ row.mode }} |
{% endfor %}
{% else %}
_No payout rows available._
{% endif %}

{% if warnings %}
## Warnings
{% for w in warnings %}
- {{ w }}
{% endfor %}
{% endif %}

## Data Lineage
- Sources: {{ sources | join(', ') if sources else 'unavailable' }}
