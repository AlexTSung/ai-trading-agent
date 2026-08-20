# 151 Trading Strategies — Complete Reference
Source: Kakushadze & Serur (2018), SSRN-3247865

---

## Chapter 3: Stocks (19 strategies)

### 3.1 Price-Momentum
Buy top-decile stocks by past return, sell bottom decile. Formation period T=12 months, skip period S=1 month.
- Monthly return: `R_i(t) = P_i(t)/P_i(t+1) - 1` (eq 266)
- Cumulative return: `R_i^cum = P_i(S)/P_i(S+T) - 1` (eq 267)
- Risk-adjusted: `R_i^risk.adj = R_i^mean / σ_i` (eq 269)
- Variance: `σ_i² = 1/(T-1) Σ(R_i(t) - R_i^mean)²` (eq 270)
**Application:** Already partially in judge.py via momentum scoring. Upgrade to use 12-month skip-1 formation window.

### 3.2 Earnings-Momentum
Buy top-decile stocks by SUE (Standardized Unexpected Earnings).
- `SUE_i = (E_i - E_i') / σ_i` (eq 274)
  where E_i = latest quarterly EPS, E_i' = EPS 4 quarters ago, σ_i = std of E_i - E_i' over 8 quarters.
**Application:** Add earnings surprise factor to research.py using Yahoo earnings data.

### 3.3 Value
Buy top-decile by Book-to-Price (B/P) ratio, sell bottom decile.
- B/P = book value per share / stock price
- Long top decile, short bottom decile (or long-only top decile)
**Application:** Add B/P ratio to quality scoring in judge.py. Yahoo Finance provides book value.

### 3.4 Low-Volatility Anomaly
Low-vol stocks historically outperform high-vol on risk-adjusted basis.
- Build portfolio long bottom-decile by σ_i (low vol), short top-decile (high vol)
- Sample window: 6-12 months (126-252 trading days)
**Application:** Penalize high-vol stocks in judge.py volatility scoring; currently partially done.

### 3.5 Implied Volatility
Stocks with rising call IV → higher future returns. Stocks with rising put IV → lower future returns.
- Buy top-decile by Δ(call IV). Short top-decile by Δ(put IV).
- Or: buy top-decile by [Δ(call IV) - Δ(put IV)]
**Application:** Future enhancement for options data integration.

### 3.6 Multifactor Portfolio
Combine multiple factor rankings (value, momentum, etc.) via:
- Demeaned ranks: `s_Ai = rank(f_Ai) - (1/N) Σ rank(f_Aj)` (eq 276)
- Combined score: `s_i = (1/F) Σ s_Ai` (eq 277)
**Application:** This is exactly what judge.py implements. Formula confirms current approach.

### 3.7 Residual Momentum
Replace raw returns with residuals from Fama-French regression.
- Regression: `R_i(t) = α_i + β_1i·MKT + β_2i·SMB + β_3i·HML + ε_i(t)` (eq 278)
- Residuals over 12m formation: `ε_i(t) = R_i(t) - β_1i·MKT - β_2i·SMB - β_3i·HML` (eq 279)
- Risk-adj residual: `R̃_i^risk.adj = ε_i^mean / σ̃_i` (eq 281)
**Application:** Advanced future enhancement. Requires Fama-French factor data.

### 3.8 Pairs Trading
Long "cheap" stock, short "rich" stock from historically correlated pair.
- Returns: `R_A = ln(P_A(t2)/P_A(t1))`, `R_B = ln(P_B(t2)/P_B(t1))` (eqs 285-286)
- Mean: `R̄ = (R_A + R_B)/2` (eq 287)
- Demeaned: `R̃_A = R_A - R̄`, `R̃_B = R_B - R̄` (eqs 288-289)
- Rich stock: demeaned return > 0 → short. Cheap: demeaned return < 0 → buy.
- Dollar neutrality: `P_A|Q_A| + P_B|Q_B| = I`, `P_A·Q_A + P_B·Q_B = 0` (eqs 290-291)
**Application:** Future strategy. Requires correlation tracking across stock universe.

### 3.9 Mean-Reversion — Single Cluster
Generalization of pairs to N>2 highly correlated stocks (same sector/industry).
- Log returns: `R_i = ln(P_i(t2)/P_i(t1))` (eq 292)
- Mean: `R̄ = (1/N) Σ R_i` (eq 293)
- Demeaned: `R̃_i = R_i - R̄` (eq 294)
- Dollar positions: `D_i = -γ R̃_i` where `γ = I / Σ|R̃_i|` (eqs 297-298)
Short stocks with R̃_i > 0, buy stocks with R̃_i < 0.

### 3.9.1 Mean-Reversion — Multiple Clusters
K clusters (sectors), treat independently. Use loadings matrix Λ_iA (binary: 1 if stock i in sector A).
- Residuals: `ε_i = R_i - R̄_G(i)` = return minus sector mean (eq 309)
- Dollar positions: `D_i = -γ ε_i` (eq 297 applied per cluster)

### 3.10 Mean-Reversion — Weighted Regression
Generalizes clustering to arbitrary loadings matrix Ω_iA (non-binary factors allowed).
- `R̃ = Z·ε`, `ε = R - Ω·Q^{-1}·Ω^T·Z·R` (eqs 314-315)
- Weights z_i can be inverse volatility: `z_i = 1/σ_i²`

### 3.11 Single Moving Average
Signal based on price vs. MA(T):
- `SMA(T) = (1/T) Σ P(t)` (eq 319)
- `EMA(T,λ) = (1-λ)/(1-λ^T) Σ λ^{t-1} P(t)` (eq 320)
- Buy if P > MA(T), sell/short if P < MA(T) (eq 321)
**Application:** Core of current entry logic. MA20 already used in trade.py.

### 3.12 Two Moving Averages
- Buy if MA(T') > MA(T), sell if MA(T') < MA(T) where T' < T (eq 322)
- With stop-loss: liquidate long if P < (1-Δ)×P_1; liquidate short if P > (1+Δ)×P_1 (eq 323)
**Application:** Already partially implemented. Consider adding MA50 crossover signal.

### 3.13 Three Moving Averages
Filter false signals using three lengths T_1 < T_2 < T_3 (e.g., 3, 10, 21):
- Long: MA(T_1) > MA(T_2) > MA(T_3) (eq 324)
- Exit long: MA(T_1) ≤ MA(T_2)
- Short: MA(T_1) < MA(T_2) < MA(T_3)
**Application:** Upgrade judge.py to require 3-MA alignment for higher conviction signals.

### 3.14 Support and Resistance (Pivot Points)
- Pivot: `C = (P_H + P_L + P_C)/3` (eq 325)
- Resistance: `R = 2C - P_L` (eq 326)
- Support: `S = 2C - P_H` (eq 327)
- Buy if P > C, liquidate long if P ≥ R (eq 328)
**Application:** Add to research.py as additional technical signal.

### 3.15 Channel (Donchian)
- Ceiling: `B_up = max(P(1), ..., P(T))` (eq 329)
- Floor: `B_down = min(P(1), ..., P(T))` (eq 330)
- Buy if P = B_down (price hits channel floor), short if P = B_up (eq 331)
**Application:** Useful for dip-buy detection. T=20 days is common.

### 3.16 Event-Driven — M&A
- Cash merger: buy target stock, hold to acquisition price
- Stock merger: buy target (A), short acquirer (B) in ratio of merger exchange rate
**Application:** Low priority — requires M&A news detection.

### 3.17 Machine Learning — KNN
Predict T-day return Y(t) = P(t-T)/P(t) - 1 using k-nearest neighbors in feature space.
- Feature variables: volume MA, price MA at multiple lookbacks (eqs 333-335)
- Normalize: `X̃_a(t) = (X_a(t) - X_a^-) / (X_a^+ - X_a^-)` (eq 337)
- KNN distance: `[D(t,t')]² = Σ(X̃_a(t) - X̃_a(t'))²` (eq 338)
- Prediction: `Y(t) = (1/k) Σ Y(t'_α(t))` (eq 339)
- Signal thresholds: Long if Y > z_1, Short if Y < -z_1 (eq 341)
**Application:** Future ML enhancement to judge.py prediction module.

### 3.18 Statistical Arbitrage — Optimization
Maximize Sharpe ratio S = P/V via portfolio weights:
- `w_i = γ Σ C_ij^{-1} E_j` (eq 350) where C is covariance matrix, E are expected returns
- Dollar-neutral solution: `w_i = (1/λ)[Σ C_ij^{-1} E_j - C_ij^{-1} Σ(C_kl^{-1} E_l)/Σ(C_kl^{-1})]` (eq 358)
**Application:** Long-term: implement portfolio-level optimization in trade.py.

### 3.19 Market-Making
Buy at bid, sell at ask. Requires directional signal to stay on right side of market.
**Application:** Not suitable for current agent architecture (requires HFT infrastructure).

### 3.20 Statistical Arbitrage — Alpha Momentum
Algorithm:
1. Compute alpha returns R_is for N stocks, M+1 days
2. Serially demean: `X_is = R_is - (1/(M+1)) Σ R_is`
3. Sample variance: `σ_i² = (1/M) Σ X_is²`
4. Normalize: `Y_is = X_is / σ_i`, keep first M columns
5. Cross-sectionally demean: `Λ_is = Y_is - (1/N) Σ Y_js`
6. Expected alpha: `E_i = (1/d) Σ R_is` (eq 360)
7. Portfolio weights: `w_i = η·ε̃_i/σ_i`

---

## Chapter 4: ETFs (6 strategies)

### 4.1 Sector Momentum Rotation
Buy top-decile sector ETFs by T-month cumulative return (T=6-12 months):
- `R_i^cum(t) = P_i(t)/P_i(t+T) - 1` (eq 361)
Hold 1-3 months.

### 4.1.1 Sector Momentum with MA Filter
Buy top-decile ETFs only if current price P > MA(T') (T'=100-200 days) (eq 362).
Short bottom-decile ETFs only if P < MA(T').

### 4.1.2 Dual-Momentum Sector Rotation
Go long top-decile sectors only if broad market index P > MA(T').
If market is below MA: rotate into uncorrelated ETF (gold/Treasury) (eq 363).

### 4.2 Alpha Rotation
Replace cumulative ETF return with alpha (Jensen's alpha) from Fama-French regression:
- `R_i(t) = α_i + β_1i·MKT + β_2i·SMB + β_3i·HML + ε_i(t)` (eq 364)
Buy top-decile ETFs by α_i.

### 4.3 R-Squared Strategy
Low R² ETFs (high "selectivity") tend to have higher future returns.
- `R² = 1 - SS_res/SS_tot` (eq 366)
- Buy low-R² + high-alpha quintile; sell high-R² + low-alpha quintile.

### 4.4 ETF Mean-Reversion
Use Internal Bar Strength (IBS):
- `IBS = (P_C - P_L)/(P_H - P_L)` (eq 370)
IBS near 1 → "rich" (sell). IBS near 0 → "cheap" (buy).

### 4.5 Leveraged ETFs (LETFs)
Short both leveraged ETF and its leveraged inverse ETF (both suffer daily rebalancing decay).
Invest proceeds in Treasury ETF. Risk: one leg can spike sharply.

### 4.6 Multi-Asset Trend Following
Long-only ETF portfolio. Filter: R_i^cum > 0 and P_i > MA_i(T').
Weight options:
- `w_i = γ_1 R_i^cum` (momentum-proportional) (eq 371)
- `w_i = γ_2 R_i^cum / σ_i` (risk-adjusted momentum) (eq 372)
- `w_i = γ_3 R_i^cum / σ_i²` (Sharpe-optimal, ignores correlations) (eq 373)

---

## Chapter 5: Fixed Income (15 strategies)

### Core Concepts
- Zero-coupon bond yield: `R(t,T) = -ln(P(t,T))/(T-t)` (eq 374)
- Coupon bond: `P_c(t,T) = P(t,T) + kδ Σ P(t,T_i)` (eq 375)
- Modified duration: `ModD(t,T) = -∂ln(P_c)/∂R` (eq 385)
- Dollar duration: `DD(t,T) = ModD(t,T)·P_c(t,T)` (eq 387)
- Convexity: `C(t,T) = -(1/P_c)·∂²P_c/∂R²` (eq 388)

### 5.2 Bullets: All bonds same maturity T
If rates expected to fall → pick longer T. If rates expected to rise → shorter T.

### 5.3 Barbells: Two maturities T_1 (short) and T_2 (long)
Same duration as bullet at T* but higher convexity → better protection vs. parallel yield shifts.
- Duration: `D = (w̃_1·T_1 + w̃_2·T_2)/(w̃_1 + w̃_2)` (eq 390)
- Convexity: `C = (w̃_1·T_1² + w̃_2·T_2²)/(w̃_1 + w̃_2)` (eq 392)

### 5.4 Ladders
n bonds with equal allocations at maturities T_1...T_n (equidistant).
Average maturity: `T = (1/n) Σ T_i` (eq 395). Diversifies interest rate and reinvestment risk.

### 5.5 Bond Immunization
Match portfolio duration to future cash obligation maturity T*.
Two-bond portfolio: `P_1 + P_2 = P`, `P_1·D_1 + P_2·D_2 = P·D` (eqs 397-398)

### 5.6 Dollar-Duration-Neutral Butterfly
Long barbell (T_1 short, T_3 long) + short bullet (T_2 mid).
- Zero cost: `P_1 + P_3 = P_2` (eq 404)
- DD neutral: `P_1·D_1 + P_3·D_3 = P_2·D_2` (eq 405)

### 5.7 Fifty-Fifty Butterfly
`P_1·D_1 = P_3·D_3 = (1/2)·P_2·D_2` (eq 406). Neutral to small yield curve steepening/flattening.

### 5.8 Regression-Weighted Butterfly
`P_1·D_1 = β·P_3·D_3` where β estimated from historical spread regressions (eqs 407-408).

### 5.9 Low-Risk Factor (Bonds)
Buy Investment Grade bonds (AAA→A-), take bottom decile by maturity.

### 5.10 Value Factor (Bonds)
Value = ln(S_i/S_i*) where S_i* = fitted credit spread from cross-sectional regression (eq 411).
Buy top decile by V_i (cheap bonds with high actual vs. theoretical spread).

### 5.11 Carry Factor (Bonds)
Carry = yield R(t,T)·Δt + roll-down return C_roll.
- `C(t, t+Δt, T) ≈ R(t,T)·Δt - ModD(t,T)·[R(t,T-Δt) - R(t,T)]` (eq 414)
Buy top-decile by carry.

### 5.12 Rolling Down the Yield Curve
Buy bonds from steepest yield curve segment. Sell as they approach maturity. Reinvest in new long/medium bonds.

### 5.13 Yield Curve Spread (Flatteners & Steepeners)
- Flattener: short spread (sell long bond, buy short bond) if rates expected to rise (eq 416)
- Steepener: buy spread if rates expected to fall

### 5.14 CDS Basis Arbitrage
CDS basis = CDS spread − bond spread (eq 417). Negative basis → bond is cheap → buy bond + CDS insurance.

### 5.15 Swap-Spread Arbitrage
Long/short swap vs. Treasury bond of same maturity. Profit if LIBOR spread changes (eqs 418-420).

---

## Chapter 6: Indexes (5 strategies)

### 6.2 Cash-and-Carry Arbitrage (Index Arbitrage)
Theoretical futures price: `F*(t,T) = [S(t) - D(t,T)]·exp(r(T-t))` (eq 421)
Basis: `B(t,T) = (F(t,T) - F*(t,T))/S(t)` (eq 422)
If B > 0: sell futures, buy index basket. If B < 0: buy futures, sell index.

### 6.3 Dispersion Trading in Equity Indexes
Long single-stock option straddles, short index option straddle.
Based on: implied index vol > theoretical index vol from constituent correlations.
`σ_I² = Σ w_i·w_j·σ_i·σ_j·ρ_ij` (eq 423)

### 6.4 Intraday ETF Arbitrage
Exploit spread between two ETFs on same index:
- Buy ETF2, short ETF1 if P1^Bid ≥ P2^Ask × κ (κ ≈ 1.002) (eq 428)
Requires fast execution.

### 6.5 Index Volatility Targeting
Maintain constant volatility σ* by periodic rebalancing:
- Risky asset allocation: `w = σ*/σ`
- Risk-free allocation: `1 - w`
Rebalance only if |Δw|/w > threshold κ to reduce transaction costs.

---

## Chapter 7: Volatility (4 strategies)

### 7.2 VIX Futures Basis Trading
Mean-reversion on VIX futures basis `B_VIX = P_UX1 - P_VIX` (eq 429).
Daily roll: `D = B_VIX / T` (eq 430).
- Long VIX futures if D < -0.10 (backwardation)
- Short VIX futures if D > 0.10 (contango, eq 431)
Hedge: short mini-S&P 500 futures vs. long VIX futures.

### 7.3 Volatility Carry (VXX/VXZ)
VXX tracks short-term VIX futures (months 1-2). VXZ tracks medium-term (months 4-7).
VXX loses value in contango (most of the time). Strategy: short VXX, long VXZ with hedge ratio h = β = ρ·σ_X/σ_Z.
Hedge weights for VIX futures: `w_i = σ_X · Σ C_ij^{-1}·σ_j·ρ_j` (eq 432).

### 7.4 Volatility Risk Premium
Sell straddles when implied vol (VIX) > realized vol. Proxy for entry: VIX at month start > realized vol of S&P 500 returns month-to-date.

### 7.4.1 Volatility Risk Premium with Gamma Hedging
Keep Delta-neutral by buying (selling) underlying when it moves up (down). Converts to "Theta play" — collect time decay while hedging directional exposure.

---

## Chapter 8: Foreign Exchange (5 strategies)
*See forex_strategies.md for detailed FX content with all formulas*

---

## Chapter 9: Commodities (6 strategies)
*See forex_strategies.md for commodity strategies*

---

## Chapter 10: Futures (10+ strategies)
*See forex_strategies.md for futures strategies*

---

## Key Formulas Summary for Judge/Screener Integration

### Position Sizing (from 3.18)
Optimal weights maximizing Sharpe:
`w_i = γ · Σ C_ij^{-1} · E_j` (eq 350)

### Multifactor Scoring (from 3.6)
Demeaned rank: `s_Ai = rank(f_Ai) - (1/N) Σ rank(f_Aj)` (eq 276)
Combined: `s_i = (1/F) Σ s_Ai` (eq 277)
→ Our judge.py weighted scoring is a direct implementation of this.

### Momentum Formation (from 3.1)
T=12 month formation, S=1 month skip.
Risk-adjusted: `R_i^risk.adj = R_i^mean / σ_i` (eq 269)

### Pivot Support/Resistance (from 3.14)
`C = (H + L + C_prev)/3`, `R = 2C - L`, `S = 2C - H` (eqs 325-327)

### IBS Mean-Reversion Signal (from 4.4)
`IBS = (Close - Low)/(High - Low)` — values near 0 → oversold buy signal

### Volatility Targeting Weight (from 6.5)
`w = σ_target / σ_realized` — scale position size inversely with recent volatility
