"""Data contracts for canonical sales entities used in ETL validation."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EntityContract:
    entity: str
    required_columns: tuple[str, ...]


DATA_CONTRACTS: dict[str, EntityContract] = {
    "reps": EntityContract("reps", ("id", "name", "email", "team_id")),
    "users": EntityContract("users", ("id", "name", "email", "position_id")),
    "teams": EntityContract("teams", ("id", "name")),
    "accounts": EntityContract("accounts", ("id", "name")),
    "deals": EntityContract("deals", ("id", "account_id", "rep_id", "stage", "amount")),
    "revenue": EntityContract("revenue", ("rep_id", "period", "amount")),
    "quotas": EntityContract("quotas", ("rep_id", "period", "amount")),
    "plans": EntityContract("plans", ("id", "name")),
    "rules": EntityContract("rules", ("id", "plan_id", "name")),
    "plan_assignments": EntityContract("plan_assignments", ("id", "plan_id", "user_id")),
    "territories": EntityContract("territories", ("id", "name")),
    "user_territory_assignments": EntityContract("user_territory_assignments", ("id", "territory_id", "user_id")),
    "sales_units": EntityContract("sales_units", ("id", "opportunity_id", "owner_user_id", "amount")),
    "sales_credits": EntityContract("sales_credits", ("id", "sales_unit_id", "user_id", "credit_percent", "credit_amount")),
    "payouts": EntityContract("payouts", ("id", "user_id", "period", "payout_amount")),
    "activities": EntityContract("activities", ("id", "deal_id", "activity_date")),
    "bookings": EntityContract("bookings", ("deal_id", "rep_id", "booking_date", "amount")),
    "churn_events": EntityContract("churn_events", ("account_id", "period", "event_type", "arr_change")),
}
