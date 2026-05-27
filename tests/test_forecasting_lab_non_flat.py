from backend.ml.forecasting_lab.models import run_model_forecast
from backend.ml.forecasting_lab.service import compare_models_for_target


def _monthly_periods(start_year: int, start_month: int, n: int) -> list[str]:
    y, m = start_year, start_month
    periods: list[str] = []
    for _ in range(n):
        periods.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            y += 1
            m = 1
    return periods


def _quarter_slot(period: str) -> int:
    month = int(period[5:7])
    return ((month - 1) % 3) + 1


def test_pipeline_naive_forecast_is_not_flat():
    periods = _monthly_periods(2024, 1, 18)
    # Upward trend + quarter-end bulge.
    history = [
        900_000, 980_000, 1_140_000,
        940_000, 1_020_000, 1_190_000,
        980_000, 1_060_000, 1_230_000,
        1_000_000, 1_090_000, 1_260_000,
        1_040_000, 1_130_000, 1_300_000,
        1_080_000, 1_160_000, 1_350_000,
    ]

    payload = run_model_forecast(
        model_name="naive",
        history_values=history,
        history_periods=periods,
        target="pipeline",
        horizon=6,
        include_lstm=False,
    )

    values = payload.get("values", [])
    assert len(values) == 6
    assert len(set(values)) > 1
    assert sum(values[2::3]) > sum(values[0::3])


def test_payout_naive_forecast_is_not_flat():
    periods = _monthly_periods(2024, 1, 15)
    # Monthly payout pattern with quarter-end acceleration.
    history = [
        7_800, 8_300, 10_100,
        8_000, 8_600, 10_500,
        8_200, 8_800, 10_900,
        8_500, 9_100, 11_400,
        8_700, 9_300, 11_700,
    ]

    payload = run_model_forecast(
        model_name="naive",
        history_values=history,
        history_periods=periods,
        target="payout",
        horizon=6,
        include_lstm=False,
    )

    values = payload.get("values", [])
    assert len(values) == 6
    assert len(set(values)) > 1
    assert sum(values[2::3]) > sum(values[0::3])


def test_payout_compare_models_produces_scored_rows_with_medium_history():
    periods = _monthly_periods(2024, 1, 18)
    history = [
        7_500, 8_000, 9_700,
        7_700, 8_200, 10_000,
        7_900, 8_500, 10_300,
        8_200, 8_700, 10_700,
        8_400, 9_000, 11_100,
        8_700, 9_300, 11_500,
    ]

    payload = compare_models_for_target(
        history_values=history,
        history_periods=periods,
        target="payout",
        horizon=6,
        include_lstm=False,
    )

    rows = payload.get("leaderboard", [])
    assert payload.get("status") == "ok"
    assert rows
    assert any((row.get("backtest") or {}).get("status") == "ok" for row in rows)
    assert any(row.get("selection_score") is not None for row in rows)


def test_pipeline_moving_average_keeps_quarter_end_uplift():
    periods = _monthly_periods(2024, 1, 24)
    history = [
        900_000, 960_000, 1_090_000,
        910_000, 980_000, 1_120_000,
        930_000, 1_000_000, 1_140_000,
        950_000, 1_020_000, 1_170_000,
        970_000, 1_040_000, 1_200_000,
        990_000, 1_060_000, 1_220_000,
        1_010_000, 1_080_000, 1_250_000,
        1_030_000, 1_100_000, 1_280_000,
    ]

    payload = run_model_forecast(
        model_name="moving_average",
        history_values=history,
        history_periods=periods,
        target="pipeline",
        horizon=6,
        include_lstm=False,
    )

    vals = payload.get("values", [])
    f_periods = payload.get("periods", [])
    assert len(vals) == 6
    assert len(f_periods) == 6
    by_slot = {1: [], 2: [], 3: []}
    for p, v in zip(f_periods, vals):
        by_slot[_quarter_slot(p)].append(float(v))

    assert by_slot[1] and by_slot[3]
    assert sum(by_slot[3]) / len(by_slot[3]) > sum(by_slot[1]) / len(by_slot[1])


def test_payout_short_history_uses_business_prior_shape():
    periods = _monthly_periods(2026, 1, 6)
    history = [10_000, 10_000, 10_000, 10_000, 10_000, 10_000]

    payload = run_model_forecast(
        model_name="naive",
        history_values=history,
        history_periods=periods,
        target="payout",
        horizon=6,
        include_lstm=False,
    )

    vals = payload.get("values", [])
    f_periods = payload.get("periods", [])
    assert len(vals) == 6
    assert len(set(vals)) > 1

    by_slot = {1: [], 2: [], 3: []}
    for p, v in zip(f_periods, vals):
        by_slot[_quarter_slot(p)].append(float(v))

    assert by_slot[1] and by_slot[3]
    assert sum(by_slot[3]) / len(by_slot[3]) > sum(by_slot[1]) / len(by_slot[1])


def test_pipeline_short_history_has_uplift_without_extreme_cliff():
    periods = _monthly_periods(2026, 1, 6)
    history = [1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000, 1_000_000]

    payload = run_model_forecast(
        model_name="naive",
        history_values=history,
        history_periods=periods,
        target="pipeline",
        horizon=6,
        include_lstm=False,
    )

    vals = payload.get("values", [])
    f_periods = payload.get("periods", [])
    assert len(vals) == 6
    assert len(set(vals)) > 1

    by_slot = {1: [], 2: [], 3: []}
    for p, v in zip(f_periods, vals):
        by_slot[_quarter_slot(p)].append(float(v))

    assert by_slot[1] and by_slot[3]
    slot1 = sum(by_slot[1]) / len(by_slot[1])
    slot3 = sum(by_slot[3]) / len(by_slot[3])
    assert slot3 > slot1

    rollover_drops: list[float] = []
    for i in range(1, len(vals)):
        prev_slot = _quarter_slot(f_periods[i - 1])
        curr_slot = _quarter_slot(f_periods[i])
        if prev_slot == 3 and curr_slot == 1 and float(vals[i - 1]) > 0.0:
            drop = (float(vals[i - 1]) - float(vals[i])) / float(vals[i - 1])
            rollover_drops.append(drop)

    assert rollover_drops
    assert max(rollover_drops) <= 0.14
