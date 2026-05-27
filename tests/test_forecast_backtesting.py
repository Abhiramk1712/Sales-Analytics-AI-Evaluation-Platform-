import pandas as pd

from backend.ml.evaluation import rolling_origin_backtest


def test_rolling_origin_backtest_ok_for_long_history():
    series = pd.Series([100000 + i * 2500 for i in range(30)])
    result = rolling_origin_backtest(series, min_train_size=18)
    assert result["status"] == "ok"
    assert result["folds"] > 0
    assert "mae" in result
    assert "rmse" in result
    assert "mape" in result
    assert "bias" in result


def test_rolling_origin_backtest_warns_for_short_history():
    series = pd.Series([100000 + i * 1000 for i in range(10)])
    result = rolling_origin_backtest(series, min_train_size=18)
    assert result["status"] == "insufficient_history"
    assert result["warnings"]
