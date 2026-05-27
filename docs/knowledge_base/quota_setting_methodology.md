# Quota Setting Methodology

## Why Quota Matters
Quota is the single most important lever in sales compensation. Poorly set quotas:
- Set above 20× annual revenue history → reps give up, attainment collapses
- Set without ramp → new reps are penalized for first 6 months
- Set as flat annual / 4 → ignores seasonality and rep history
- Set too low → top performers hit 150%+ and commission expense exceeds plan

## Formula-Based Quota Setting

### Step 1: Establish Historical Revenue Baseline
Use the **70th percentile** of a rep's last 12 months of monthly revenue.
P70 is more stable than average (less affected by outlier months) and sets
a challenging but achievable benchmark for mid-level performers.

### Step 2: Apply Growth Factor
Multiply by the company's target growth rate:
- 10% YoY growth: growth_factor = 1.10
- 20% YoY growth: growth_factor = 1.20
- 25% YoY growth: growth_factor = 1.25

### Step 3: Annualize / Quarterize
- Annual quota = P70_monthly × 12 × growth_factor
- Quarterly quota = P70_monthly × 3 × growth_factor

### Step 4: Apply Ramp Factor
For reps hired within the last 6 months:

| Months Since Hire | Ramp Factor |
|---|---|
| 0 | 25% |
| 1 | 35% |
| 2 | 50% |
| 3 | 65% |
| 4 | 75% |
| 5 | 85% |
| 6+ | 100% |

### Step 5: Floor Minimum
Minimum quarterly quota should be at least $50,000 regardless of ramp or history.

## Quota Attainment Distribution Benchmarks (Healthy SaaS)
- Above 120%: 10–20% of reps (accelerator-earning)
- 100–120%: 25–35% of reps (on-target)
- 75–100%: 20–30% of reps (near-target)
- 50–75%: 10–15% of reps (below target, needs coaching)
- Below 50%: < 10% of reps (critical, possible quota issue or rep issue)

If > 30% of reps are below 50% attainment, the quota-setting process should be reviewed.
If > 40% of reps are above 120%, quotas may be set too low.

## Quota At Risk Definition
A rep is "quota at risk" when, at any point in a period:
1. Attainment < 60% of period quota (proportional to elapsed time)
2. Pipeline coverage < 2× remaining quota
3. Activity rate declining (fewer than 2 activities per week)

RevOps should flag these reps weekly and assign coaching resources.

## Common Quota Anti-Patterns
1. **Manager's Intuition Quota**: No data backing — creates unfair inconsistency
2. **Last Year +20%**: Does not account for territory changes, ramp, or product shifts
3. **Top-Down Allocation**: Company target ÷ headcount ignores regional/segment differences
4. **Same Quota for New and Veteran Reps**: Penalizes new hires, rewards low-ambition veterans
5. **No Mid-Year Adjustment**: Quota should be reviewed at H1 if territory or market changes significantly
