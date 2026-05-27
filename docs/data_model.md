# Data Model Overview

This document summarizes the public-facing analytical data model used in this project.

## Core Domains

- Organization: teams, reps
- Commercial: accounts, deals, activities, bookings, churn events
- Compensation: plans, rules, plan assignments, sales units, sales credits, payouts
- Performance: revenue, quotas, attainment snapshots

## Key Tables and Purpose

- `teams`: Sales team master data.
- `reps`: Individual sellers linked to teams.
- `accounts`: Customer/account entities.
- `deals`: Opportunity pipeline and stage progression.
- `revenue`: Periodic recognized revenue facts.
- `quotas`: Rep quota targets by period.
- `plans`: Compensation plans.
- `rules`: Compensation rules for each plan.
- `plan_assignments`: User-to-plan assignment history.
- `sales_units`: Booked sales units linked to opportunities.
- `sales_credits`: Credit allocation for sales units.
- `payouts`: Computed payout outcomes by user/plan/period.
- `bookings`: Booking facts for ARR/MRR style analysis.
- `churn_events`: ARR decrease/increase events for retention analysis.

## Relationship Summary

- `reps.team_id -> teams.id`
- `deals.account_id -> accounts.id`
- `deals.rep_id -> reps.id`
- `rules.plan_id -> plans.id`
- `plan_assignments.plan_id -> plans.id`
- `sales_units.opportunity_id -> deals.id`
- `sales_credits.sales_unit_id -> sales_units.id`
- `payouts.plan_id -> plans.id`
- `bookings.deal_id -> deals.id`

## Analytical Layer Mapping

The dbt modeling layer groups these into:

- Staging (`stg_*`): Direct source normalization.
- Intermediate (`int_*`): Attainment, plan coverage, payout signal logic, baseline forecasting math.
- Marts (`mart_*`): Executive KPIs, rep performance, payout readiness, anomalies, pipeline health.
