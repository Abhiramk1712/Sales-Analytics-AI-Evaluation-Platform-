# Forecasting Assumptions

## Revenue Forecast Model

### Model Components
1. **SARIMAX** (Seasonal ARIMA with exogenous variables)
   - Captures seasonal patterns (12-month cycle for annual business)
   - Captures trend and residual variation
   - Order: SARIMAX(1,1,1)(1,0,1,12)

2. **Ridge Regression**
   - Uses engineered lag features (1, 2, 3, 12-month lags)
   - Rolling averages (3, 6-month)
   - Calendar features (month, trend)
   - Regularization (alpha=0.3) to prevent overfitting

### Ensemble Weights
- Weights determined by inverse RMSE on held-out test period
- Dynamically balanced based on recent model performance

### Confidence Intervals
- 90% confidence bands
- Widened beyond SARIMAX to account for ensemble uncertainty

### Limitations
- Requires minimum 14 months of historical data
- Assumes future resembles past patterns
- Does not account for step changes (M&A, market disruption)
- Less reliable for short forecast horizons (<3 months ahead)

### Recommended Uses
- Strategic planning and budgeting
- Identify trend changes early
- Not suitable for tactical deal-level decisions
