"""
backend/ml/lstm_forecaster.py
=============================
Optional LSTM-based forecaster.

Design goals:
- Use LSTM when torch is available.
- Degrade gracefully to baseline if torch is not installed.
- Keep runtime bounded for API usage.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.neural_network import MLPRegressor
from sklearn.preprocessing import MinMaxScaler

try:
    import torch
    import torch.nn as nn
    from torch.utils.data import DataLoader, TensorDataset

    TORCH_AVAILABLE = True
    TORCH_IMPORT_ERROR = ""
except Exception as exc:  # pragma: no cover - environment-specific
    torch = None
    nn = None
    DataLoader = None
    TensorDataset = None
    TORCH_AVAILABLE = False
    TORCH_IMPORT_ERROR = str(exc)


DEFAULT_WINDOW = 12


@dataclass
class LSTMTrainResult:
    status: str
    model: Any
    scaler: MinMaxScaler | None
    window: int
    train_loss: float | None
    val_loss: float | None
    warnings: list[str]
    model_kind: str = "torch_lstm"


if TORCH_AVAILABLE:
    class LSTMRegressor(nn.Module):
        def __init__(self, input_size: int = 1, hidden_size: int = 32, num_layers: int = 1):
            super().__init__()
            self.lstm = nn.LSTM(input_size=input_size, hidden_size=hidden_size, num_layers=num_layers, batch_first=True)
            self.fc = nn.Linear(hidden_size, 1)

        def forward(self, x):
            out, _ = self.lstm(x)
            return self.fc(out[:, -1, :])
else:
    class LSTMRegressor:  # pragma: no cover - no-torch fallback type
        pass


def prepare_sequences(series: list[float] | np.ndarray, window: int = DEFAULT_WINDOW) -> tuple[np.ndarray, np.ndarray, MinMaxScaler]:
    """Convert a 1D series into LSTM windowed samples and return fitted scaler."""
    values = np.asarray(series, dtype=float).reshape(-1, 1)
    scaler = MinMaxScaler()
    scaled = scaler.fit_transform(values).flatten()

    X: list[np.ndarray] = []
    y: list[float] = []
    for i in range(window, len(scaled)):
        X.append(scaled[i - window:i])
        y.append(float(scaled[i]))

    if not X:
        return np.empty((0, window, 1), dtype=np.float32), np.empty((0,), dtype=np.float32), scaler

    X_arr = np.array(X, dtype=np.float32).reshape(-1, window, 1)
    y_arr = np.array(y, dtype=np.float32)
    return X_arr, y_arr, scaler


def _baseline_forecast(series: np.ndarray, horizon: int) -> list[float]:
    if len(series) == 0:
        return [0.0 for _ in range(horizon)]
    trailing = series[-3:] if len(series) >= 3 else series
    avg = float(np.mean(trailing))
    return [round(avg, 2) for _ in range(horizon)]


def train_lstm(
    series: list[float] | np.ndarray,
    epochs: int = 100,
    batch_size: int = 32,
    window: int = DEFAULT_WINDOW,
) -> LSTMTrainResult:
    """Train an LSTM model and return artifact metadata."""
    values = np.asarray(series, dtype=float)
    warnings: list[str] = []

    if not TORCH_AVAILABLE:
        warnings.append(f"torch not available: {TORCH_IMPORT_ERROR}")
        warnings.append("Using MLP-based sequence surrogate instead of torch LSTM.")

        X, y, scaler = prepare_sequences(values, window=window)
        if len(X) < 20:
            return LSTMTrainResult(
                status="insufficient_data",
                model=None,
                scaler=scaler,
                window=window,
                train_loss=None,
                val_loss=None,
                warnings=warnings + ["Not enough sequence samples for surrogate sequence model training."],
                model_kind="mlp_sequence",
            )

        X_flat = X.reshape(len(X), -1)
        split_idx = max(int(len(X_flat) * 0.8), 1)
        X_train, y_train = X_flat[:split_idx], y[:split_idx]
        X_val, y_val = X_flat[split_idx:], y[split_idx:]

        try:
            model = MLPRegressor(
                hidden_layer_sizes=(64, 32),
                activation="relu",
                solver="adam",
                max_iter=600,
                random_state=42,
                early_stopping=True,
                n_iter_no_change=20,
            )
            model.fit(X_train, y_train)

            train_pred = model.predict(X_train)
            train_loss = float(mean_squared_error(y_train, train_pred))

            final_val = None
            if len(X_val) > 0:
                val_pred = model.predict(X_val)
                final_val = float(mean_squared_error(y_val, val_pred))

            return LSTMTrainResult(
                status="trained",
                model=model,
                scaler=scaler,
                window=window,
                train_loss=train_loss,
                val_loss=final_val,
                warnings=warnings,
                model_kind="mlp_sequence",
            )
        except Exception as exc:
            return LSTMTrainResult(
                status="unavailable",
                model=None,
                scaler=scaler,
                window=window,
                train_loss=None,
                val_loss=None,
                warnings=warnings + [f"MLP surrogate training failed: {exc}"],
                model_kind="mlp_sequence",
            )

    if len(values) < (window + 12):
        warnings.append("Insufficient history for robust LSTM training; fallback recommended.")

    X, y, scaler = prepare_sequences(values, window=window)
    if len(X) < 20:
        return LSTMTrainResult(
            status="insufficient_data",
            model=None,
            scaler=scaler,
            window=window,
            train_loss=None,
            val_loss=None,
            warnings=warnings + ["Not enough sequence samples for LSTM training."],
        )

    split_idx = max(int(len(X) * 0.8), 1)
    X_train, y_train = X[:split_idx], y[:split_idx]
    X_val, y_val = X[split_idx:], y[split_idx:]

    train_ds = TensorDataset(torch.tensor(X_train), torch.tensor(y_train))
    train_loader = DataLoader(train_ds, batch_size=min(batch_size, len(train_ds)), shuffle=True)

    model = LSTMRegressor()
    criterion = nn.MSELoss()
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)

    best_val = float("inf")
    best_state = None
    patience = 10
    no_improve = 0

    for _ in range(max(5, epochs)):
        model.train()
        for xb, yb in train_loader:
            optimizer.zero_grad()
            out = model(xb).squeeze(-1)
            loss = criterion(out, yb)
            loss.backward()
            optimizer.step()

        if len(X_val) == 0:
            continue

        model.eval()
        with torch.no_grad():
            val_pred = model(torch.tensor(X_val)).squeeze(-1)
            val_loss = float(criterion(val_pred, torch.tensor(y_val)).item())

        if val_loss < best_val:
            best_val = val_loss
            best_state = model.state_dict()
            no_improve = 0
        else:
            no_improve += 1
            if no_improve >= patience:
                break

    if best_state is not None:
        model.load_state_dict(best_state)

    model.eval()
    with torch.no_grad():
        train_pred = model(torch.tensor(X_train)).squeeze(-1)
        train_loss = float(criterion(train_pred, torch.tensor(y_train)).item())

    final_val = None
    if len(X_val) > 0:
        with torch.no_grad():
            val_pred = model(torch.tensor(X_val)).squeeze(-1)
            final_val = float(criterion(val_pred, torch.tensor(y_val)).item())

    return LSTMTrainResult(
        status="trained",
        model=model,
        scaler=scaler,
        window=window,
        train_loss=train_loss,
        val_loss=final_val,
        warnings=warnings,
        model_kind="torch_lstm",
    )


def forecast_lstm(
    model: Any,
    scaler: MinMaxScaler,
    series: list[float] | np.ndarray,
    horizon: int = 6,
    window: int = DEFAULT_WINDOW,
    model_kind: str = "torch_lstm",
) -> list[float]:
    """Generate iterative multi-step LSTM forecast."""
    if model is None or scaler is None:
        return _baseline_forecast(np.asarray(series, dtype=float), horizon)

    values = np.asarray(series, dtype=float).reshape(-1, 1)
    if len(values) == 0:
        return [0.0 for _ in range(horizon)]

    scaled_all = scaler.transform(values).flatten().tolist()
    seq = scaled_all[-window:] if len(scaled_all) >= window else ([scaled_all[0]] * (window - len(scaled_all)) + scaled_all)

    preds_scaled: list[float] = []
    for _ in range(horizon):
        if model_kind == "mlp_sequence":
            x = np.array(seq, dtype=np.float32).reshape(1, window)
            yhat = float(model.predict(x)[0])
        elif TORCH_AVAILABLE:
            x = torch.tensor(np.array(seq, dtype=np.float32).reshape(1, window, 1))
            with torch.no_grad():
                yhat = float(model(x).item())
        else:
            return _baseline_forecast(np.asarray(series, dtype=float), horizon)

        preds_scaled.append(yhat)
        seq = seq[1:] + [yhat]

    preds = scaler.inverse_transform(np.array(preds_scaled, dtype=float).reshape(-1, 1)).flatten()
    return [round(max(float(v), 0.0), 2) for v in preds]


def backtest_lstm(series: list[float] | np.ndarray, window: int = DEFAULT_WINDOW) -> dict[str, Any]:
    """Holdout backtest for LSTM with baseline fallback."""
    values = np.asarray(series, dtype=float)
    if len(values) < max(window + 8, 20):
        return {
            "status": "insufficient_history",
            "warnings": ["Insufficient history for LSTM backtest"],
        }

    test_horizon = min(6, max(3, len(values) // 5))
    train = values[:-test_horizon]
    test = values[-test_horizon:]

    trained = train_lstm(train, epochs=60, batch_size=16, window=window)
    if trained.status != "trained":
        preds = _baseline_forecast(train, test_horizon)
        warnings = trained.warnings + ["LSTM unavailable; used baseline forecast for backtest."]
    else:
        preds = forecast_lstm(
            trained.model,
            trained.scaler,
            train,
            horizon=test_horizon,
            window=window,
            model_kind=trained.model_kind,
        )
        warnings = trained.warnings

    y_true = np.asarray(test, dtype=float)
    y_pred = np.asarray(preds, dtype=float)
    err = y_pred - y_true

    mae = float(mean_absolute_error(y_true, y_pred))
    rmse = float(np.sqrt(mean_squared_error(y_true, y_pred)))
    mape = float(np.mean(np.abs(err / (y_true + 1e-9))) * 100)

    return {
        "status": "ok",
        "folds": int(test_horizon),
        "mae": round(mae, 4),
        "rmse": round(rmse, 4),
        "mape": round(mape, 4),
        "warnings": warnings,
    }


def run_lstm_forecast(series: list[float] | np.ndarray, horizon: int = 6) -> dict[str, Any]:
    """End-to-end LSTM forecast entrypoint with graceful fallback behavior."""
    values = np.asarray(series, dtype=float)
    warnings: list[str] = []

    if len(values) == 0:
        return {
            "strategy_used": "unavailable",
            "forecast_values": [],
            "backtest": {"status": "insufficient_history", "warnings": ["No history available"]},
            "warnings": ["No history available for LSTM forecast."],
            "history_months": 0,
            "torch_available": TORCH_AVAILABLE,
        }

    trained = train_lstm(values, epochs=80, batch_size=16, window=DEFAULT_WINDOW)
    warnings.extend(trained.warnings)

    if trained.status != "trained":
        forecast_values = _baseline_forecast(values, horizon)
        strategy_used = "lstm_fallback_baseline"
        warnings.append("Falling back to baseline forecast because LSTM training is unavailable.")
    else:
        forecast_values = forecast_lstm(
            model=trained.model,
            scaler=trained.scaler,
            series=values,
            horizon=horizon,
            window=trained.window,
            model_kind=trained.model_kind,
        )
        strategy_used = "lstm" if trained.model_kind == "torch_lstm" else "sequence_mlp"

    backtest = backtest_lstm(values, window=DEFAULT_WINDOW)

    return {
        "strategy_used": strategy_used,
        "forecast_values": forecast_values,
        "backtest": backtest,
        "warnings": warnings + list(backtest.get("warnings", [])),
        "history_months": int(len(values)),
        "torch_available": TORCH_AVAILABLE,
        "sequence_available": trained.status == "trained",
        "sequence_backend": trained.model_kind if trained.status == "trained" else "baseline",
    }
