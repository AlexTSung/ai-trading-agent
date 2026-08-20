# Community & Web Strategy Reference
Sources: PapersWithBacktest, Quantpedia, MQL5, TradingView, Web Research
Compiled: 2026-07-06

---

## TREND FOLLOWING & MOMENTUM

### MA Cross (Simple Moving Average Crossover)
**Logic:** Buy when fast MA crosses above slow MA; sell/short when it crosses below.
**Standard parameters:**
- Short-term: SMA 20, 50
- Long-term: SMA 100, 150, 200
- Golden cross: SMA50 > SMA200 → long signal
- Death cross: SMA50 < SMA200 → short/exit signal
**Breakout variants:**
- Price breaks above SMA20/50/100/150/200 → momentum entry
- Price bounces off SMA → mean-reversion entry
**Bounces:** Price pulls back to MA and holds → continuation entry
**Already in agent:** MA20 pullback entry (trade.py). Consider adding MA50/200 regime filter.

---

### EMA Cross (Exponential Moving Average Crossover)
**Logic:** Same as SMA cross but EMA reacts faster to recent price.
**Common pairings:**
- EMA 9 / EMA 21 (short-term swing)
- EMA 12 / EMA 26 (MACD basis)
- EMA 50 / EMA 200 (macro trend)
**Advantage over SMA:** Less lag, smoother reaction to trend changes.
**MQL5 "Simple EMA Cross EA":** Configurable fast/slow EMA with stops and lot sizing.

---

### MACD (Moving Average Convergence Divergence)
**Formula:**
- MACD Line = EMA(12) − EMA(26)
- Signal Line = EMA(9) of MACD Line
- Histogram = MACD Line − Signal Line
**Default params:** 12, 26, 9
**Buy signals:**
1. MACD line crosses above signal line (bullish crossover)
2. MACD histogram flips from negative to positive
3. Bullish divergence: price makes lower low, MACD makes higher low
**Sell signals:**
1. MACD line crosses below signal line
2. Bearish divergence: price makes higher high, MACD makes lower high
**Rule of thumb:** Don't open longs when MACD is below zero line.
**Stop loss:** Below entry candle low (longs), above entry candle high (shorts).
**Take profit:** 3× stop loss distance or nearest key level.
**Backtest (S&P 500, default params):** CAGR 4.79%, avg gain/trade 0.95%, profit factor 4.22, max drawdown 16%.
**Better settings for swing trading:** 19, 39, 9 (slower, fewer false signals).

---

### RSI (Relative Strength Index)
**Formula:** RSI = 100 − 100/(1 + RS), where RS = avg gain / avg loss over N periods.
**Default period:** 14. Better for mean reversion: 2-3 periods.
**Standard thresholds:** Overbought >70, Oversold <30.
**Strategies:**
1. **Classic:** Buy RSI < 30 (oversold), sell RSI > 70 (overbought)
2. **2-Period RSI mean reversion:** Buy RSI(2) < 10 on SPY/large-caps; historically high win rate
3. **RSI range-momentum:** Buy RSI crosses above 50 (momentum), short below 50
4. **RSI + ADX filter:** RSI signals only valid when ADX < 25 (ranging market)
**Backtest:** 2-period RSI strategy on S&P 500: CAGR ~12.1%, only invested 38% of time.
**LRSI (Laguerre RSI):** Smoother version using gamma parameter (γ=0.5 typical). Less whipsaw.

---

### MACD + RSI Combo (referenced as "ingpawat" / "Mike_Trdw")
- MACD gives trend direction confirmation
- RSI gives entry timing within trend
- Entry: MACD > signal line AND RSI < 40 (pullback in uptrend) → buy
- Exit: RSI > 70 OR MACD crosses below signal

---

### ATR Breakout Strategy (Away-Box793)
**ATR formula:** ATR(14) = EMA of True Range over 14 periods.
True Range = max(High−Low, |High−PrevClose|, |Low−PrevClose|)
**Breakout rules:**
- Upper channel: SMA(N) + K × ATR(14)
- Lower channel: SMA(N) − K × ATR(14)
- K typically 1.5–3.0
- Buy when price breaks above upper channel
- Sell/short when price breaks below lower channel
- Exit when price reverts inside channel
**Stop loss:** 1.5× ATR from entry.
**Position sizing:** `shares = risk_per_trade / (K × ATR)` → volatility-normalized.
**Breakout filter:** Only trade when weekly ATR is at multi-year lows (compression before explosion).
**MQL5 "ADX Trend Pullback EA":** ADX > 25 + EMA pullback + ATR stops.

---

### New High / New Low Breakout
**52-week high breakout:** Buy stocks making 52-week highs (momentum tends to continue).
**52-week low:** Short stocks making 52-week lows (or buy extreme capitulation with filters).
**Donchian Channel (§3.15 from PDF):** Buy at N-day low, short at N-day high.
**Range breakout:** Buy when price breaks above a defined consolidation range high.
**Compression breakout types:**
- Horizontal compression: price coiling in tight range → buy breakout
- Wedge compression: ascending/descending wedge → buy breakout of wedge
- Open Range Breakout (ORB): see below

---

### VWAP Strategy (Volume Weighted Average Price)
**Formula:** `VWAP = Σ(Price × Volume) / Σ(Volume)` — resets each session.
**Role:** Intraday "fair value" anchor; institutional benchmark price.
**Mean reversion setup:**
- Buy when price ≥ 2σ below VWAP + RSI < 30 (oversold confirmation)
- Sell/short when price ≥ 2σ above VWAP + RSI > 70
- Target: return to VWAP
**Trend setup:**
- Long: price above VWAP + pull back to VWAP + hold → buy
- Short: price below VWAP + rally to VWAP + reject → sell
**Filters:** Works best in choppy/range-bound sessions; avoid in strong trending days.
**MQL5 "MNQ VWAP Trend":** Session VWAP bounces + ADX/EMA gating + rejection candles.

---

### Opening Range Breakout (ORB)
**Definition:** High and low of first N minutes after open = "Opening Range."
**Parameters:** N = 5, 15, or 30 minutes (15 minutes is most common starting point).
**Entry rules:**
- Define ORH (Opening Range High) and ORL (Opening Range Low)
- Buy on close above ORH with volume > 1.5× average (RVOL > 1.5)
- Short on close below ORL with volume confirmation
- Long only if price is above VWAP; short only if price is below VWAP
**Stop loss:** Place at ORL (for longs) or ORH (for shorts).
**Range filter:** Skip if OR width > 2% of stock price (too wide = bad R/R).
**Position sizing:** Risk 1% of account; shares = (account × 0.01) / OR_width.
**Profit target:** 1.5–2× risk (R:R of 1:1.5 to 1:2).
**Win rate expectation:** 40–60% (winners run to cover losses in trend days).
**Best markets:** US stocks, S&P 500, Nasdaq futures.
**MQL5 "V1N1 LONNY":** Asian Range Breakout on M15/M30/H1 using PSAR+MACD+Stoch.

---

### Seasonal Strategy: Sell in May and Stay Away
**Rule:** Invest Nov–Apr, hold cash/bonds May–Oct.
**Backtest (1950–2025, S&P 500):**
- Nov–Apr average return: ~7.0% annualized
- May–Oct average return: ~2.1% annualized
- $10K invested only winters since 1950 → ~$1.04M
- $10K invested only summers since 1950 → ~$15K
**Global evidence:** Effect holds in 36 of 37 countries studied, documented since 1694 in UK.
**Implementation:**
- Exit equities on April 30 close → enter cash/Treasury ETF
- Re-enter equities on November 1 open
- Refinement: exit only if SPY < SMA(10M) to avoid missing late bull runs
**Caveats:** Summer months had positive returns 38 of 50 years (avg +4.25%); strategy misses bull summers.

---

## MEAN REVERSION

### Mean Reversion to Trend (Mike_Trdw)
**Logic:** In an established uptrend, buy pullbacks back toward the trend line/MA.
**Entry:**
1. Confirm uptrend: price > MA200 AND MA50 > MA200
2. Wait for pullback to MA20 or MA50
3. Buy when price bounces (IBS < 0.3 or RSI(2) < 20 on daily)
**Exit:** Return to recent highs or fixed R:R (1:2).

### Mean Reversion in Range (The-Goat-Trader)
**Logic:** Price oscillates between support and resistance in a defined range.
**Entry:**
- Buy near support with RSI(2) < 10 or IBS < 0.15
- Short near resistance with RSI(2) > 90 or IBS > 0.85
**Range identification:** BB width contracting + ADX < 20
**Exit:** Target midpoint of range or opposite band.

### Reverting Market (The-Goat-Trader)
**Logic:** After large moves (>1.5% intraday), fade the move.
**Entry:**
- Day was up >1.5%: short at close, cover next open
- Day was down >1.5%: buy at close, sell next open
**Filter:** Only trade when VIX < 20 (low regime vol) and market above MA200.
**Backtest note:** Mean reversion works best in low-vol, sideways markets.

---

## MOMENTUM ROTATION (TACTICAL ALLOCATION)

### Ned Davis / Meb Faber 10-Month TAA
**Reference:** Meb Faber "A Quantitative Approach to Tactical Asset Allocation" (SSRN-962461)
**Rule:** At month-end, compare price to 10-month SMA (≈ 200-day MA).
- If price > SMA(10M): stay long
- If price < SMA(10M): exit to cash/bonds
**Asset universe:** 5 ETFs: US stocks (SPY), foreign stocks (EFA), bonds (IEF), REITs (VNQ), commodities (DJP)
**Performance:** Near-equity returns with near-bond drawdowns; max drawdown reduced from 46% to <10%.
**Rebalance:** Monthly, last trading day of month.

### Gary Antonacci GEM (Global Equity Momentum)
**Reference:** Antonacci (2012), dual-momentum concept
**Universe:** SPY (US equities), EFA (international equities), AGG (US Aggregate Bonds)
**Rules (monthly rebalance):**
1. Compute 12-month return for SPY vs. T-bills (absolute momentum)
2. If SPY − T-bills < 0 → hold AGG (bonds)
3. If SPY − T-bills > 0 → compare SPY vs. EFA return:
   - SPY > EFA: hold SPY
   - EFA > SPY: hold EFA
**Backtest (1950–2018):** Significantly outperforms S&P 500 with lower drawdown.
**Robustness:** Tested across 324 parameter combinations (1–18 month lookbacks); effect persists.

### Andreas Clenow Momentum Rotation ("Stocks on the Move")
**Universe:** S&P 500 constituents
**Momentum formula:** `score = annualized_slope(90d) × R²(90d)` (quality-adjusted momentum)
**Entry rules:**
- Rank all S&P 500 stocks by score
- Buy top 100 (top 20%) by score
- Only buy if stock is above its 100-day MA
- Only buy if no gap >15% in last 90 days
- Only trade if SPY > SMA(200)
**Position sizing:** `shares = account_value × 0.001 / ATR(20)` (target 10bps daily move per position)
**Execution:** Trade on Wednesdays, rebalance twice monthly
**Bonds fallback:** When SPY < SMA(200), rotate to bonds if bond trend positive.

### Momentum Rotation (The-Goat-Trader)
**Logic:** Rotate capital monthly into top N performers across asset classes.
**Universe:** 6–10 ETFs covering equities, bonds, gold, real estate, commodities
**Score:** 1-month + 3-month + 6-month return (weighted or averaged)
**Rule:** Hold top 3 by score; exit anything below bottom 3; rebalance monthly.
**Dual momentum filter:** Hold asset only if absolute return (vs. T-bill) is positive.

---

## GRID TRADING (Mike_Trdw)

**What it is:** Place buy/sell limit orders at fixed price intervals above and below current price.
**Parameters:**
- Grid spacing: 20–30 pips (EUR/USD), or 0.5–1% for stocks
- Grid levels: 5–15 orders per side
- Lot size: 0.01–0.1 per level (smaller = safer)
- Capital requirement: levels × lot × margin × 2–3× safety buffer
**When it works:** Sideways/ranging markets with no strong trend.
**When it breaks:** Strong directional trend (all orders on wrong side accumulate losses).
**Risk control:**
- Define max total loss threshold (e.g., 5% of account) → close all if hit
- Never run grid without total loss stop
- Avoid around major news events (FOMC, NFP, CPI)
**TradingView:** "Grid Like Strategy" by alexgrover — includes optional martingale, warns of exponential risk.
**MQL5 "XANDER Grid XAUUSD":** Bidirectional grid for gold with daily profit targets and drawdown protection.
**MQL5 "BGC Grid EA":** CUSUM change-point detection adapts grid to regime.

---

## ARBITRAGE

### Index Fund Rebalancing Arbitrage
**Logic:** Index additions/deletions force passive funds to buy/sell in size → predictable price impact.
**Rules:**
1. Monitor S&P 500 reconstitution announcements
2. Buy stocks to be added immediately after announcement
3. Short stocks to be deleted immediately after announcement
4. Close positions the day before the change becomes effective
**Performance:** ~5.4% abnormal return per trade (after borrowing costs).
**Risk:** Crowding — many arbs do this; alpha decays as trade becomes popular.
**Timing:** Much of price movement concentrates in last 30–60 min before close on effective date.
**Half of day's volume** can occur in final minute.

### Triangular Arbitrage (FX)
*See forex_strategies.md §8.5 for full formula (eq 453)*
**Quick rule:** USD → EUR → GBP → USD. If product of Bid rates × (1/Ask) > 1 → free profit.
**Reality:** Opportunities last milliseconds; need co-located execution.

### Pairs Trading / Offset Trades
*See trading_strategies_reference.md §3.8 for full formula*
**Quick rule:** Two historically correlated stocks. If spread > 2σ → buy cheap, short rich.

---

## SMART MONEY CONCEPTS (SMC / ICT)
*Recommended reading: Franco_Love*

**Origin:** Michael J. Huddleston ("ICT" — Inner Circle Trader).
**Core premise:** Follow institutional order flow; trade from zones where smart money entered.

**Key components:**
1. **Market Structure:** BOS (Break of Structure) = trend continuation; CHOCH (Change of Character) = reversal.
2. **Order Blocks (OB):** Last opposing candle before strong impulsive move.
   - Bullish OB: last bearish candle before sharp rally → price returns here to buy
   - Bearish OB: last bullish candle before sharp drop → price returns here to sell
3. **Fair Value Gaps (FVG):** 3-candle formation where wicks of candles 1 and 3 don't overlap body of candle 2. Price typically returns to "fill" the gap.
4. **Liquidity Pools:** Clusters of stop-losses above swing highs or below swing lows. Institutions "sweep" liquidity before reversing.
5. **Premium/Discount Zones:** Above equilibrium (50% of range) = premium → sell. Below = discount → buy.

**Entry template:**
1. Identify HTF (4H/Daily) market structure
2. Find Order Block or FVG at premium/discount zone
3. Enter on LTF (15M/5M) BOS confirmation
4. Stop: beyond Order Block. Target: next liquidity pool or HTF structure level.

**Python library:** `pip install smartmoneyconcepts` (GitHub: joshyattridge/smart-money-concepts)
**TradingView:** "SMC Clean 4H Zones" by ionplesu88 — Fair Value Gaps + Order Blocks + BOS/CHOCH.

---

## SCALPING

**Definition:** Very short-term trades (seconds to minutes). Target: 2–10 pips or 0.1–0.3%.
**Key techniques:**
- VWAP scalp: buy at VWAP touch in uptrend, sell 3–5 ticks above
- Level 2 tape reading: buy when large bid appears; sell when large ask absorbs
- ORB micro-scalp: trade first breakout candle with tight stop
**Requirements:** Low spread broker, direct access routing, fast execution.
**Not practical for automated agent at current stage** — requires tick-by-tick data and sub-second execution.

---

## PRICE PATTERNS / CANDLESTICK

### Key Candlestick Patterns
| Pattern | Type | Signal |
|---|---|---|
| Doji | Single | Indecision; reversal when at extremes |
| Hammer | Single | Bullish reversal (long lower wick at bottom) |
| Shooting Star | Single | Bearish reversal (long upper wick at top) |
| Engulfing | Two-bar | Bullish/bearish depending on direction |
| Inside Bar | Two-bar | Compression → breakout |
| Morning/Evening Star | Three-bar | Reversal |

**Inside Bar** (MQL5): last candle's high < prior candle's high AND low > prior candle's low → pending breakout above high or below low.

### Double Top / Bottom (TradingView "Bjorgum Double Tap")
- Double top: two highs near same level → short breakdown below neckline
- Double bottom: two lows near same level → buy breakout above neckline
- Confirm with volume spike on breakout

### Gap Filling (TradingView "Gap Filling Strategy")
- Exhaustion gap (at end of trend): fades quickly → trade reversion
- Runaway gap (mid-trend): hold direction
- Breakaway gap (start of trend): enter in gap direction
- Rule: buy morning gap-down, sell on gap fill to prior close (if > 0.5% gap size)

---

## MARTINGALE
*Listed as risky but educational — reckless_homicide*

**Core rule:** Double position size after each loss.
**Position after n losses:** `size = initial × 2^n`
**Expected behavior:** One win recovers all prior losses and returns initial profit unit.
**Risk reality:**
- 7 consecutive losses on a $25K account at 1% initial risk → account wiped
- 7-loss streaks are normal in most trading strategies
- Exponential drawdown: 10 losses = 1024× initial size
**Safer variant — Anti-Martingale:** Double after wins, halve after losses (let winners run, cut losers).
**Practical compromise:** Fractional scaling — increase 50% (not 100%) after each loss; cap at 4× initial.
**Use case in agent:** Never use for directional trading. Could be studied for grid recovery logic only with hard total-loss stop.

---

## QUANTPEDIA SCREENER — TOP STRATEGIES BY PERFORMANCE

| Strategy | Asset | Return | Volatility | Frequency |
|---|---|---|---|---|
| Volatility Risk Premium | Equities/Options | 26.0% | 19.0% | Monthly |
| Time Series Momentum | Multi-asset | 20.7% | 15.7% | Monthly |
| Trend-Following Effect | Equities | 19.3% | 15.6% | Daily |
| Short Term Reversal | Equities | 16.3% | 14.9% | Weekly |
| Sector Momentum Rotational | Equities | 13.9% | 18.4% | Monthly |
| Momentum Asset Allocation | Equities | 14.5% | 11.0% | Monthly |
| Momentum in Commodities | Commodities | 14.6% | 25.6% | Monthly |
| Low Volatility Factor | Equities | 11.3% | 10.1% | Monthly |
| Asset Class Trend-Following | Multi-asset | 11.3% | 6.9% | Monthly |
| Pairs Trading (Stocks) | Equities | 11.2% | 5.9% | Daily |
| FX Carry Trade | Currencies | 7.3% | 9.6% | Monthly |
| Overnight Seasonality (BTC) | Crypto | 33.0% | 20.9% | Intraday |

---

## PAPERSWITHBACKTEST — TOP STRATEGIES BY SHARPE

| Strategy | Asset | Sharpe | Notes |
|---|---|---|---|
| European Size Premium | Equities | 1.87 | Size factor, Western Europe |
| Investment CAPM | Equities | 1.80 | Profitability + investment factors |
| Good Carry, Bad Carry | FX | 1.73 | Quality-filtered G10 carry |
| Dual-Class Share Voting | Equities | 1.68 | Voting premium arbitrage |
| Cryptocurrency Factor Investing | Crypto | 1.52 | Value + momentum + carry in crypto |
| Regime-Dependent Carry Trades | FX | 1.51 | Vol regime switching |
| Optimal Trend Following Rules | Equities | 1.14 | Theoretically optimal EMA/MACD params |

---

## TRADINGVIEW EDITOR'S PICKS — KEY STRATEGIES

| Strategy | Logic | Asset |
|---|---|---|
| TASC 1% A Week | Mean reversion on TQQQ, target 1% weekly | Leveraged ETFs |
| TrendMaster Pro | MA + BB + RSI + MACD + Stoch confluence | Multi-asset |
| Bjorgum Double Tap | Double top/bottom with zigzag | Multi-asset |
| ATR Trailing Stop + EMA | ATR stops + 200 EMA regime filter | Multi-asset |
| MNQ VWAP Trend | VWAP bounces + ADX/EMA | MNQ Futures |
| MNQ Elite Hybrid | ORB + session VWAP directional filter | MNQ Futures |
| Apex Fusion AI | EMA + SuperTrend + MACD + Fibonacci + volume | Multi-asset |
| PMax Explorer | ATR + MA reversal screener for 20 tickers | Multi-asset |
| Predictive Vol Breakout | 100-pt scoring: vol + momentum + ADX + regression | BTC/Gold/Index |

---

## MQL5 EXPERT ADVISORS — NOTABLE PATTERNS

| EA | Key Logic |
|---|---|
| ADX Trend Pullback | ADX trend filter + EMA pullback entries + ATR stops |
| VR RSI Robot | Sync H1 + D1 RSI extremes for entry |
| Liquidity Sweep H4-M15 | H4 swing high/low sweep → M15 breakout |
| KSQ Fair Value Gap | Institutional FVG zones + regime detection |
| OHLCMTF Scalper | Multi-timeframe OHLC price action |
| RSI Grid EA Pro | RSI entries + adaptive grid recovery |
| BGC Grid EA | CUSUM change-point detection + equity-cycle grid |
| Hon Matrix | Switches between trending/ranging logic with news filter |
| Inside Bar EA | Inside bar continuation + ATR quality filter |
| Weekly Day Reversal | Turnaround Tuesday — buy bearish Mondays, sell bullish Mondays |

---

## MULTI-TIMEFRAME NOTE
*From BT_2112:* Most of these strategies improve significantly when applied simultaneously across multiple timeframes. Higher-TF confirms direction; lower-TF provides entry precision.

**Application to agent:**
- Daily chart: trend direction (MA200, sector momentum)
- 4H chart: entry zone (order blocks, FVG, MA20 pullback)
- 1H chart: entry trigger (RSI, MACD, ORB)

---

## OPTIONS STRATEGIES (mentioned by community)

| Strategy | Logic | Risk Profile |
|---|---|---|
| Covered Calls (Chuyito) | Own stock + sell OTM call. Collect premium; capped upside. | Low risk |
| The Wheel (faancy5050) | Sell CSP → get assigned → sell CC → repeat on large-caps. | Moderate |
| Short Iron Condor SPY 0DTE | Sell OTM call + put spreads expiring same day on SPY. | High theta, tail risk |
| Long Straddle/Strangle Earnings | Buy both call + put before earnings; profit from large move. | Defined risk, volatility bet |

**Note:** These require options data feed. Not in scope for current agent but document for future phase.

---

## RESOURCES FOR FURTHER RESEARCH
- [PapersWithBacktest](https://paperswithbacktest.com/) — 5,000+ papers with Python code (paid)
- [Quantpedia Screener](https://quantpedia.com/screener) — 1,000+ quantitative strategies with metrics
- [MQL5 Codebase](https://www.mql5.com/en/code/mt5/experts) — Free MT5 Expert Advisors
- [TradingView Strategies](https://www.tradingview.com/scripts/?script_type=strategies) — Pine Script strategies
- [TradingView Editor's Picks](https://www.tradingview.com/scripts/editors-picks/?script_type=strategies) — Curated best
- [Antonacci GEM](https://www.optimalmomentum.com/global-equities-momentum/) — Dual momentum framework
- [Meb Faber TAA](https://mebfaber.com/timing-model/) — 10-month TAA model
- [Clenow Momentum](https://www.followingthetrend.com/stocks-on-the-move/) — Stocks on the Move
- [SMC Python Library](https://github.com/joshyattridge/smart-money-concepts) — ICT concepts in Python
