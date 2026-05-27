# Payout Methodology

## Overview

This document describes how sales compensation is computed in the system.
All figures in individual rep payout responses come from the database — never from this document.

## Data Flow

```
SalesCredit (what the rep is credited for)
  → PlanAssignment (which comp plan applies to this rep)
    → Plan.Rules (rate tiers, thresholds, accelerators)
      → CreditPayoutResult (computed payout record)
```

## Fallback Mode

When SalesCredit rows are not present, the system falls back to **rep-level revenue/quota aggregates**.
Fallback results carry: `fallback_mode = "rep_level_estimate"` and a warning label.
Fallback payouts should be treated as directional estimates, not precise payout calculations.

## Commission Tiers

Tiers are defined per comp plan in the `rules` table with metric_name = `attainment_pct`.
Example (illustrative structure — actual rates come from the database):

| Attainment | Commission Rate |
|------------|----------------|
| 0–79%      | 0%             |
| 80–99%     | 8%             |
| 100–109%   | 10%            |
| ≥ 110%     | 12% (accelerator) |

## Accelerators

Accelerators apply to the **overage amount above quota** when attainment > 100%.
Defined in rules with metric_name = `accelerator`.

## SPIFFs

One-time cash bonuses for specific behaviors (product focus, multi-year deals, etc.).
Applied additionally on top of base commission.

## Clawbacks

Negative adjustments for customer churn within a clawback window (typically 90 days).
Applied as a deduction from the final payout.

## Output Fields

| Field              | Description                                    |
|--------------------|------------------------------------------------|
| credited_amount    | Revenue the rep is credited for this period    |
| quota              | Rep's quota for the period                     |
| attainment         | credited_amount / quota × 100                  |
| base_commission    | Commission from plan rules                     |
| accelerator_amount | Bonus for exceeding quota                      |
| spiff_amount       | SPIFF bonuses earned                           |
| clawback_amount    | Deductions for churned accounts                |
| final_payout       | base + accelerator + spiff − clawback          |
| confidence         | high / medium / low based on data completeness |
| fallback_mode      | none / rep_level_estimate / no_rules_fallback  |

## Important Notes

- Payout numbers displayed in the UI come from live DB computations, not this document.
- This document is for agent methodology explanation only.
- Do not cite specific dollar amounts from this document in agent responses.
