from backend.ml.forecasting_lab.model_selection import build_leaderboard, choose_best_model


def test_build_leaderboard_uses_none_for_unscored_models():
    model_results = {
        "naive": {
            "forecast": {"strategy_used": "naive", "warnings": []},
            "backtest": {"status": "insufficient_history", "warnings": ["short history"]},
        },
        "ridge": {
            "forecast": {"strategy_used": "ridge", "warnings": []},
            "backtest": {
                "status": "ok",
                "mape": 8.0,
                "smape": 9.0,
                "bias_pct": 1.0,
                "directional_accuracy": 70.0,
                "warnings": [],
            },
        },
    }

    leaderboard = build_leaderboard(model_results)

    assert leaderboard[0]["model"] == "ridge"
    assert leaderboard[0]["selection_score"] is not None
    assert leaderboard[1]["model"] == "naive"
    assert leaderboard[1]["selection_score"] is None


def test_choose_best_model_falls_back_when_all_unscored():
    leaderboard = [
        {"model": "naive", "selection_score": None},
        {"model": "moving_average", "selection_score": None},
    ]

    assert choose_best_model(leaderboard) == "naive"
