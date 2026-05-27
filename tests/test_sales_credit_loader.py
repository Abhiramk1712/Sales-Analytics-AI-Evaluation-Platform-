import inspect

import backend.data_generator as dg


def test_load_csvs_into_database_includes_sales_credit_ecosystem_tables():
    src = inspect.getsource(dg._load_csvs_into_database)

    required_csvs = [
        '"bookings.csv"',
        '"churn_events.csv"',
        '"arr_waterfall.csv"',
        '"sales_units.csv"',
        '"sales_credits.csv"',
        '"sales_unit_line_items.csv"',
        '"payouts.csv"',
    ]
    for csv_name in required_csvs:
        assert csv_name in src

    required_models = [
        "Booking(",
        "ChurnEvent(",
        "ArrWaterfallEntry(",
        "SalesUnit(",
        "SalesUnitLineItem(",
        "SalesCredit(",
        "PayoutRecord(",
    ]
    for model_ctor in required_models:
        assert model_ctor in src
