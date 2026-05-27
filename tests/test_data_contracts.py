from backend.etl.contracts import DATA_CONTRACTS


def test_core_entity_contracts_exist():
    required = {
        "reps", "users", "teams", "accounts", "deals", "revenue", "quotas",
        "plans", "rules", "plan_assignments", "territories", "user_territory_assignments",
        "sales_units", "sales_credits", "payouts",
        "activities", "bookings", "churn_events",
    }
    assert required.issubset(set(DATA_CONTRACTS.keys()))


def test_each_contract_has_required_columns():
    for key, contract in DATA_CONTRACTS.items():
        assert contract.required_columns, f"{key} has no required columns"
