# Forex & Multi-Asset Strategies Reference
Source: Kakushadze & Serur (2018), SSRN-3247865, Chapters 8-10

Platform: OANDA (oandapyV20 package, practice account)
Implementation target: forex_screener.py, forex_research.py, forex_judge.py, forex_trade.py

---

## Chapter 8: Foreign Exchange

### 8.1 Moving Averages with HP Filter
Remove high-frequency noise before MA crossover using Hodrick-Prescott filter.

**HP Filter:**
`g = Σ[S(t) - S*(t)]² + λ·Σ[S*(t+1) - 2S*(t) + S*(t-1)]²` → minimize (eq 437)
- S(t) = observed FX spot rate, S*(t) = smoothed trend
- For monthly data: λ = 100·n² = 14,400 (n = periods per year)
- For daily data: λ ≈ 14,400 / (12²) × (252²) ≈ 6.25M (scale by frequency²)

**Signal:**
- Buy currency if MA(T1) of HP-filtered rate > MA(T2) of HP-filtered rate (T1 < T2)
- Sell/short if MA(T1) < MA(T2)
- Typical: T1=10 days, T2=30 days

**Python:** `from scipy.signal import butter` or `statsmodels.tsa.filters.hp_filter`

---

### 8.2 Carry Trade
Borrow low-interest currency, invest in high-interest currency.

**Uncovered Interest Rate Parity (UIRP):**
`(1 + r_d) = E_t(S(t+T))/S(t) × (1 + r_f)` (eq 438)

**Covered Interest Rate Parity (CIRP):**
`F(t,T) = S(t) × (1 + r_d)/(1 + r_f)` (eq 441)
- F(t,T) = forward exchange rate
- S(t) = spot rate (domestic per foreign)
- r_d = domestic interest rate, r_f = foreign interest rate

**Forward discount (carry signal):**
`D(t,T) = s(t) - f(t,T) ≈ r_f - r_d` (eqs 442-443)
where s(t) = ln(S(t)), f(t,T) = ln(F(t,T))

**Implementation:**
- T = 1, 3, 6, or 12 months
- Use overnight/1-week swap rates as r_d, r_f proxies
- OANDA provides swap/rollover rates per pair
- **Long** the currency with higher interest rate (positive carry)
- **Short** the currency with lower interest rate

---

### 8.2.1 High-Minus-Low (HML) Carry
Cross-sectional strategy across multiple currency pairs.

**Forward discount per pair i:**
`D_i(t,T) = s_i(t) - f_i(t,T) ≈ r_f^i - r_d^i` (eqs 442-443)

**Signal:**
- Sort all N pairs by D_i
- **Long** top quantile (high forward discount = high r_f relative to r_d)
- **Short** bottom quantile (low forward discount = high r_d relative to r_f)
- Rebalance monthly

**Pairs to track:**
- EUR/USD, GBP/USD, AUD/USD, NZD/USD (tend to be high carry)
- USD/JPY, USD/CHF (tend to be low carry / funding currencies)

---

### 8.3 Dollar Carry Trade
Average cross-sectional forward discount:
`D̄(t,T) = (1/N) Σ D_i(t,T)` (eq 444)

**Signal:**
- If D̄ > 0: go **long all N** forward contracts (buy high-yield basket vs. USD)
- If D̄ < 0: go **short all N** forward contracts (sell high-yield basket vs. USD)
- T = 1, 2, 3, 6, or 12 months

---

### 8.4 Momentum + Carry Combo
Combine momentum strategy return R1 and carry strategy return R2:
`R(t_s) = w1·R1(t_s) + w2·R2(t_s)`

**Optimal weights (minimum variance):**
`w1 = (σ2² - σ1·σ2·ρ) / (σ1² + σ2² - 2σ1σ2ρ)` (eq 451)
`w2 = (σ1² - σ1·σ2·ρ) / (σ1² + σ2² - 2σ1σ2ρ)` (eq 452)

where σ1, σ2 = std of R1, R2 returns, ρ = correlation between R1 and R2.

**Implementation:**
- Estimate σ1, σ2, ρ from trailing 252-day window
- Rebalance weights monthly
- This is the primary FX strategy to implement first

---

### 8.5 Triangular Arbitrage
Exploit mispricing across three currencies A, B, C.

**Profit condition:**
`R(A→B→C→A) = Bid(A→B) × Bid(B→C) × (1/Ask(C→A)) > 1` (eq 453)

**Example:** USD → EUR → GBP → USD
- If USD→EUR (bid) × EUR→GBP (bid) × (1/(GBP→USD ask)) > 1 → profitable

**OANDA pairs needed:** EUR/USD, GBP/USD, EUR/GBP (or any three-way set)

**Note:** Real opportunities are milliseconds-wide in live markets. More practical in paper trading for learning. Check at each new tick/bar.

---

## Chapter 9: Commodities

### 9.1 Roll Yield (Backwardation vs. Contango)
`φ = P1/P2` where P1 = front-month futures, P2 = next-month futures (eq 454)
- φ > 1 (backwardation): near-term premium → **buy**
- φ < 1 (contango): near-term discount → **sell**

**Implementation:** Trade commodity ETFs (GLD, USO, UNG) using futures curve data.

### 9.2 Hedging Pressure (COT Report)
Use CFTC Commitments of Traders data:
- Speculator HP = long contracts / (long + short contracts)
- High speculators' HP + low hedgers' HP = **buy signal**

### 9.4 Value (5-Year Relative)
`v_i = P5/P0` (eq 455) where P5 = price 5 years ago, P0 = current price.
- Buy top tercile (cheap vs. 5-year history)
- Sell bottom tercile (expensive vs. 5-year history)

### 9.5 Skewness Premium
`S_i = (1/(σ_i³·T)) Σ[R_is - R̄_i]³` (eq 456)
- **Buy** bottom quintile by skewness (negative skew → positive premium)
- **Sell** top quintile by skewness (positive skew → negative premium)

### 9.6 Ornstein-Uhlenbeck Pricing Model
Mean-reverting process: `dX(t) = κ[a - X(t)]dt + σ·dW(t)` (eq 459)
- κ = mean-reversion speed, a = long-term mean, σ = volatility, W = Brownian motion

**Futures price:**
`ln(F(t,T)) = exp(-κ(T-t))·X(t) + a·[1-exp(-κ(T-t))] + σ²/(4κ)·[1-exp(-2κ(T-t))]` (eq 462)

**Application:** Model currency pair mean-reversion for carry trade entry/exit timing.

---

## Chapter 10: Futures

### 10.1 Hedge Ratio — Conversion Factor Model
Bond futures hedge ratio:
`h_C = C·(M_B/M_F)` (eq 467)
where C = conversion factor, M_B = face value of bond, M_F = face value of futures contract.

Modified duration hedge ratio:
`h_D = β·(D_B/D_F)` (eq 468)
where β = regression beta, D_B = bond duration, D_F = futures duration.

### 10.3 Contrarian Mean-Reversion (Futures)
Market index return: `R_m = (1/N) Σ R_i` (eq 469)
Weights: `w_i = -γ·[R_i - R_m]` (eq 470), normalized so `Σ|w_i| = 1`
Short outperformers, buy underperformers relative to market.

### 10.4 Trend Following (Futures)
`η_i = sign(R_i)` (eq 474)
`w_i = γ·(η_i/σ_i)` (eq 475) — buy winners, sell losers, scaled by inverse vol

**Dollar-neutral version:**
`w_i = γ·[η_i/σ_i - (1/N)·Σ η_j/σ_j]` (eq 477)

**Application (FX):** Use sign of 20-day return × inverse ATR as position weight.

---

## Forex Implementation Roadmap

### Phase 1: Core Infrastructure
```
forex_screener.py
  - scan_pairs(pairs_list)           # get OANDA bid/ask + recent bars
  - compute_carry(pair)              # estimate D_i from swap rates
  - rank_by_carry(pairs)             # HML carry sort
  - detect_ma_crossover(pair)        # HP-filter + MA(10/30)

forex_research.py
  - get_oanda_bars(pair, granularity, count)
  - compute_atr(bars, period=14)
  - compute_momentum(bars, T=20)
  - compute_hp_filter(prices, lam=6250000)  # daily λ
  - get_swap_rates(pair)             # carry proxy from OANDA

forex_judge.py
  - score_carry(pair_data)           # +/- carry signal 0-10
  - score_momentum(pair_data)        # trend direction score
  - score_combo(pair_data)           # eq 451-452 combined weight
  - gate_decision(scores)            # approve/reject trade

forex_trade.py
  - place_fx_order(pair, units, side)  # via oandapyV20
  - set_stop_loss(pips=50)
  - run_fx_session()
```

### Phase 2: Strategy Priority Order
1. **8.4 Momentum+Carry Combo** — most robust, combines two uncorrelated edges
2. **8.2.1 HML Carry** — cross-sectional, easy to rank
3. **8.1 HP-filtered MA** — trend following, familiar pattern
4. **8.3 Dollar Carry** — directional macro bet
5. **8.5 Triangular Arb** — opportunistic, add as scanner

### Carry Rate Sources for OANDA
- OANDA practice API returns swap rates (financing rates) per pair via v20 pricing endpoint
- USD/JPY: USD rate - JPY rate → typically positive carry for long USD/JPY
- AUD/USD: AUD rate - USD rate → typically positive carry for long AUD
- Use OANDA's `/v3/accounts/{id}/pricing` with `includeUnitsAvailable=True`

### Risk Parameters for FX
- Max position per pair: 2% of account equity (tighter than stocks due to leverage)
- Stop loss: 50 pips (major pairs), 75 pips (crosses)
- Max open pairs simultaneously: 5
- Avoid trading within 30min of major data releases (NFP, FOMC, CPI)
- Session hours: London (8AM-4PM GMT) + NY overlap (1PM-5PM GMT) best liquidity

### OANDA API Quick Reference (oandapyV20)
```python
import oandapyV20
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.instruments as instruments

client = oandapyV20.API(access_token=ACCESS_TOKEN, environment="practice")

# Get candles
r = instruments.InstrumentsCandles(
    instrument="EUR_USD",
    params={"granularity": "H1", "count": 500}
)
client.request(r)
bars = r.response["candles"]

# Place market order
data = {
    "order": {
        "type": "MARKET",
        "instrument": "EUR_USD",
        "units": "1000",      # positive = buy, negative = sell
        "stopLossOnFill": {"price": "1.0800"},
        "timeInForce": "FOK"
    }
}
r = orders.OrderCreate(account_id=ACCOUNT_ID, data=data)
client.request(r)
```

### Major Forex Pairs to Trade (OANDA instrument names)
High liquidity: EUR_USD, GBP_USD, USD_JPY, USD_CHF, AUD_USD, USD_CAD, NZD_USD
Crosses: EUR_GBP, EUR_JPY, GBP_JPY, AUD_JPY
Emerging: USD_MXN, USD_SGD, USD_HKD
