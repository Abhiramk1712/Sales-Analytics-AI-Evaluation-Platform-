# Rep Performance Report — {{ rep.name }} | {{ period }}
_Generated: {{ generated_at }}_

{% if warnings %}
> **⚠️ Data Warnings**
{% for w in warnings %}- {{ w }}
{% endfor %}
{% endif %}

## Performance Summary

| Metric | Value |
|--------|-------|
| Revenue | **${{ "{:,.0f}".format(metrics.revenue) }}** |
| Quota | **${{ "{:,.0f}".format(metrics.quota) }}** |
| Attainment | **{{ metrics.attainment_pct | round(1) }}%** |
| Win Rate | **{{ metrics.win_rate | round(1) }}%** |
| Deals Won | **{{ metrics.deals_won }}** |
| Avg Deal Size | **${{ "{:,.0f}".format(metrics.avg_deal_size) }}** |

## Payout Summary

| Component | Amount |
|-----------|--------|
| Base Commission | ${{ "{:,.0f}".format(payout.base_commission) }} |
| Accelerator | ${{ "{:,.0f}".format(payout.accelerator_amount) }} |
| Bonus | ${{ "{:,.0f}".format(payout.bonus_amount) }} |
| Clawback | -${{ "{:,.0f}".format(payout.clawback_amount) }} |
| **Total Payout** | **${{ "{:,.0f}".format(payout.final_payout) }}** |

## Won Deals This Period
{% if won_deals %}
| Deal | Amount | Close Date |
|------|--------|------------|
{% for d in won_deals %}
| {{ d.name }} | ${{ "{:,.0f}".format(d.amount) }} | {{ d.actual_close_date }} |
{% endfor %}
{% else %}
_No closed-won deals this period._
{% endif %}

## Data Lineage
- Period: 
- Confidence: {{ metrics.confidence }}
- Fallback: {{ metrics.fallback_mode }}
