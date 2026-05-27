# Metric Definitions

## Core Sales Metrics

### Total Revenue
**What it measures**: Sum of all closed deal amounts

**Formula**: `SUM(revenue.amount WHERE deal.stage='Closed Won')`

**Caveats**: 
- Excludes open deals
- Only includes booked/recognized revenue
- May be subject to true-ups or adjustments

### Quota Attainment
**What it measures**: Percentage of assigned quota achieved

**Formula**: `actual_revenue / quota * 100`

**Caveats**:
- Assumes quota is assigned for the period
- May vary by region or business unit

### Win Rate
**What it measures**: Percentage of closed deals that were won

**Formula**: `count(won) / count(closed) * 100`

**Caveats**:
- Only considers deals in terminal stages
- Excludes stalled or open opportunities

### Pipeline Coverage
**What it measures**: Ratio of open pipeline to quarterly quota

**Formula**: `open_pipeline_value / (quota / 4)`

**Best Practice**: Maintain 3x or higher to ensure healthy growth

### Average Deal Size
**What it measures**: Mean value of closed-won deals

**Formula**: `SUM(deal_amount | stage=Closed Won) / COUNT(deals | stage=Closed Won)`

**Caveats**: Outliers can skew average; consider median for better picture
