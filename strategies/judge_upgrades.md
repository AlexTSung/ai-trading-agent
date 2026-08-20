# Judge.py Upgrades from 151 Strategies Research
Priority-ordered enhancements derived from Kakushadze & Serur (2018)

---

## HIGH PRIORITY — Easy wins, immediate impact

### 1. IBS Oversold Signal (from §4.4)
Internal Bar Strength: `IBS = (Close - Low) / (High - Low)`
- IBS < 0.2 → strong mean-reversion buy signal (price closed near day's low)
- IBS > 0.8 → overbought signal
- Add to research.py: compute daily IBS, pass to judge as `ibs_signal`
- In judge.py: if IBS < 0.2, add +0.5 to technical score

### 2. Pivot Point Support/Resistance (from §3.14)
`C = (H + L + Close) / 3`, `R1 = 2C - L`, `S1 = 2C - H`
- Add to research.py `compute_technicals()`:
  ```python
  pivot = (prev_high + prev_low + prev_close) / 3
  resistance1 = 2 * pivot - prev_low
  support1 = 2 * pivot - prev_high
  near_support = current_price <= support1 * 1.005  # within 0.5% of support
  ```
- In judge.py: if `near_support=True` and trend is up, add +0.3 to technical score

### 3. Three-MA Alignment Filter (from §3.13)
Require MA(5) > MA(20) > MA(50) for strong buy signal.
- Add `ma5`, `ma50` to `compute_technicals()` return
- In judge.py: 
  - MA5 > MA20 > MA50 → full momentum score
  - MA5 > MA20 but MA20 < MA50 → reduce momentum score by 30% (early trend)
  - MA5 < MA20 → bearish, reduce score by 50%

### 4. Volatility-Scaled Position Sizing (from §6.5)
Target volatility instead of fixed percentages.
In trade.py, replace fixed conviction sizing with:
```python
VOL_TARGET = 0.15  # 15% annualized target vol per position
annual_vol = research_data['volatility_30d'] * (252 ** 0.5)
vol_scalar = VOL_TARGET / max(annual_vol, 0.05)
position_pct = base_pct * min(vol_scalar, 2.0)  # cap at 2x
```

### 5. Earnings Momentum SUE Score (from §3.2)
Standardized Unexpected Earnings: `SUE = (EPS_actual - EPS_4Q_ago) / σ_eps`
- Yahoo Finance provides EPS history via `yf.Ticker(sym).earnings_history`
- Add to research.py: compute SUE if earnings data available
- In judge.py: SUE > 1.0 → add +0.5 to news score; SUE < -1.0 → subtract 0.5

---

## MEDIUM PRIORITY — More work but significant alpha

### 6. Residual Momentum (from §3.7)
Compute 12-month returns after removing market beta.
```python
# In research.py
import yfinance as yf
spy_returns = yf.download('SPY', period='13mo', interval='1mo')['Close'].pct_change()
stock_returns = yf.download(symbol, period='13mo', interval='1mo')['Close'].pct_change()
beta = np.cov(stock_returns[1:], spy_returns[1:])[0,1] / np.var(spy_returns[1:])
residual_returns = stock_returns[1:] - beta * spy_returns[1:]
residual_momentum = residual_returns[:-1].mean() / residual_returns[:-1].std()
```
Use `residual_momentum` instead of raw momentum in judge scoring.

### 7. Value Factor B/P (from §3.3)
Book-to-Price from Yahoo Finance:
```python
info = yf.Ticker(sym).info
book_per_share = info.get('bookValue', None)
bp_ratio = book_per_share / current_price if book_per_share else None
```
High B/P (cheap by fundamentals) → add to quality score in judge.py.

### 8. Donchian Channel Dip Detection (from §3.15)
```python
# 20-day channel
high_20 = max(prices[-20:])
low_20 = min(prices[-20:])
channel_pct = (current_price - low_20) / (high_20 - low_20)
# channel_pct near 0 → near 20-day low → potential buy
```
Complement to existing dip_buy logic. channel_pct < 0.05 → near channel floor.

---

## LOW PRIORITY — Complex, future phases

### 9. Pairs Trading (from §3.8)
Requires maintaining correlation matrix across stock universe.
Pairs with ρ > 0.85 (same sector): track spread, trade when >2σ from mean.
Implementation: `pairs_trading.py` standalone module.

### 10. KNN ML Predictor (from §3.17)
Use sklearn KNeighborsRegressor with features: [price_ma_ratio_5, 10, 20, 50], [vol_ratio], [rsi].
Train on 2-year history per stock, predict 20-day forward return.
Implementation: `ml_predictor.py` standalone module.

### 11. Statistical Arbitrage Optimization (from §3.18)
Full Markowitz optimization across portfolio using covariance matrix.
Requires stable covariance estimates (factor model preferred over sample covariance).
Implementation: `portfolio_optimizer.py` standalone module.
