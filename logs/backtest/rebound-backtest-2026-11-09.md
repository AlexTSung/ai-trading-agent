# Oversold-rebound backtest — 2026-11-09

Selection uses data **before 2023-01-01 only**; the chosen config is then judged once on the untouched holdout 2023-01-01 to the latest data. All returns are net of 10 bps slippage on entry plus 10 bps cost per side. Signals use the close of day t, entry is the open of t+1, exits use daily highs/lows (stop first when both are touched; gaps through the stop fill at the open). One open position per symbol.

## Data and caveats that matter

- Keyless Yahoo daily bars, split- and dividend-adjusted. 505 symbols loaded (503 S&P 500 members as of today, 23 core names), 2001 to 2026-11-09.
- **Universe B (S&P 500) has survivorship bias**: it holds only today's members, i.e. companies that did not fail or get dropped. Any buy-the-dip rule is flattered by this. A rough size of the drift: a random 10-session entry in B beat SPY by +0.15% per trade on average. Read B's excess returns net of that.
- **Universe A (the 23 core names) is hindsight-picked**: the list was chosen in Nov 2026, and several names (HOOD, RKLB, ASTS, GEV) have only a few years of history.
- **Multiple testing**: 3888 configurations were searched (108 entry x 36 exit); 3508 met the minimum sample (200 trades, 60 signal dates) in-sample. The best of that many is expected to look good by chance; the holdout is the only honest number.
- Windows overlap and signals cluster in selloffs, so the effective sample is far smaller than n. Confidence intervals resample **signal dates**, not trades.
- Events in the last 15 sessions are dropped (their windows are incomplete), so the most recent weeks are not in the sample. Yahoo adjusted bars are not point-in-time clean; $10 / $20M liquidity filters use split-adjusted values.
- Implementation parity: 325 indicator, 325 setup and 186 trade checks of the vectorised backtest against the stdlib live code, 0 mismatches.

## Selection (in-sample, universe B, before 2023)

Ranked by the lower bound (mean excess return vs SPY minus one clustered standard error).

| rank | entry rule | exit rule | n | mean vs SPY | LCB | mean ret | win |
|---|---|---|---|---|---|---|---|
| 1 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 2xATR, target +8%, 15 sessions | 262 | +1.41% | +0.96% | +2.25% | 67% |
| 2 | RSI<25, >=12% below MA50, above SMA200, no stabilization, no market filter | stop 2xATR, target +8%, 15 sessions | 399 | +1.34% | +0.89% | +2.24% | 68% |
| 3 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 2xATR, target MA20, 10 sessions | 262 | +1.31% | +0.81% | +2.14% | 60% |
| 4 | RSI<30, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 2xATR, target +8%, 15 sessions | 366 | +1.17% | +0.77% | +1.91% | 65% |
| 5 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 10%, target MA20, 10 sessions | 263 | +1.22% | +0.70% | +2.04% | 61% |
| 6 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 2xATR, no target, 10 sessions | 262 | +1.28% | +0.70% | +2.13% | 59% |
| 7 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 2xATR, target +8%, 10 sessions | 272 | +1.12% | +0.70% | +1.84% | 64% |
| 8 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 7%, target +8%, 15 sessions | 294 | +1.09% | +0.68% | +1.69% | 60% |
| 9 | RSI<30, >=12% below MA50, above SMA200, no stabilization, no market filter | stop 2xATR, target +8%, 15 sessions | 590 | +1.02% | +0.67% | +1.63% | 65% |
| 10 | RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on | stop 2xATR, target MA20, 15 sessions | 251 | +1.21% | +0.65% | +2.37% | 62% |

Of the 3508 qualifying configs, 7% had a positive in-sample LCB. Selection decay: the in-sample top 50 had a median holdout excess return of +0.32% (in-sample median +0.96%); 44 of 50 were positive on the holdout.

**Chosen:** RSI<25, >=12% below MA50, above SMA200, no stabilization, market filter on; stop 2xATR, target +8%, 15 sessions.

**Runner-up (different entry rule):** RSI<25, >=12% below MA50, above SMA200, no stabilization, no market filter; stop 2xATR, target +8%, 15 sessions.

## Chosen config vs naive rebound vs the current trend rule

### Universe B: S&P 500 today (survivorship-biased)

| set | n | win | mean | median | PF | hold | avg MAE | mean vs SPY | 95% CI vs SPY (date-clustered) |
|---|---|---|---|---|---|---|---|---|---|
| Chosen rebound, in-sample < 2023 | 262 | 67% | +2.25% | +7.78% | 1.91 | 8.6 | -5.4% | +1.41% | [+0.52%, +2.31%] |
| Runner-up rebound, in-sample < 2023 | 399 | 68% | +2.24% | +7.78% | 1.83 | 7.8 | -5.8% | +1.34% | [+0.46%, +2.19%] |
| Naive RSI<30 (chosen exits), in-sample < 2023 | 41127 | 52% | +0.33% | +0.60% | 1.12 | 9.2 | -4.9% | -0.12% | [-0.23%, -0.02%] |
| Naive RSI<30 (stop 10%, 10 sessions), in-sample < 2023 | 38081 | 52% | +0.18% | +0.23% | 1.07 | 9.2 | -5.1% | -0.17% | [-0.33%, -0.01%] |
| Current trend rule (stop 10%, 10 sessions), in-sample < 2023 | 77432 | 52% | +0.06% | +0.20% | 1.03 | 9.7 | -3.8% | -0.19% | [-0.24%, -0.14%] |
| Chosen rebound, HOLDOUT 2023+ | 166 | 60% | +0.91% | +7.78% | 1.24 | 8.0 | -7.2% | +0.21% | [-1.35%, +1.66%] |
| Runner-up rebound, HOLDOUT 2023+ | 192 | 64% | +1.44% | +7.78% | 1.43 | 7.8 | -6.8% | +0.64% | [-0.84%, +1.91%] |
| Naive RSI<30 (chosen exits), HOLDOUT 2023+ | 11030 | 51% | +0.61% | +0.37% | 1.25 | 9.8 | -4.3% | -0.48% | [-0.71%, -0.27%] |
| Naive RSI<30 (stop 10%, 10 sessions), HOLDOUT 2023+ | 10425 | 52% | +0.54% | +0.28% | 1.24 | 9.6 | -4.3% | -0.41% | [-0.65%, -0.17%] |
| Current trend rule (stop 10%, 10 sessions), HOLDOUT 2023+ | 18034 | 51% | +0.28% | +0.16% | 1.13 | 9.7 | -4.1% | -0.39% | [-0.54%, -0.25%] |

### Universe A: the 23 core names

| set | n | win | mean | median | PF | hold | avg MAE | mean vs SPY | 95% CI vs SPY (date-clustered) |
|---|---|---|---|---|---|---|---|---|---|
| Chosen rebound, in-sample < 2023 | 17 | 59% | +1.54% | +7.78% | 1.59 | 9.2 | -7.5% | +0.92% | [-2.44%, +4.12%] |
| Runner-up rebound, in-sample < 2023 | 26 | 54% | +0.29% | +3.90% | 1.08 | 9.4 | -7.7% | -0.46% | [-4.27%, +3.26%] |
| Naive RSI<30 (chosen exits), in-sample < 2023 | 1760 | 52% | +0.12% | +0.50% | 1.04 | 8.9 | -5.3% | -0.30% | [-0.59%, +0.01%] |
| Naive RSI<30 (stop 10%, 10 sessions), in-sample < 2023 | 1700 | 48% | +0.04% | -0.30% | 1.01 | 8.9 | -5.5% | -0.24% | [-0.63%, +0.20%] |
| Current trend rule (stop 10%, 10 sessions), in-sample < 2023 | 2994 | 52% | +0.37% | +0.23% | 1.18 | 9.7 | -4.1% | +0.12% | [-0.10%, +0.33%] |
| Chosen rebound, HOLDOUT 2023+ | 27 | 85% | +5.12% | +7.78% | 3.51 | 5.1 | -7.2% | +4.48% | [+1.90%, +6.86%] |
| Runner-up rebound, HOLDOUT 2023+ | 31 | 87% | +5.58% | +7.78% | 4.14 | 5.0 | -6.5% | +4.86% | [+2.50%, +7.02%] |
| Naive RSI<30 (chosen exits), HOLDOUT 2023+ | 512 | 61% | +1.65% | +4.64% | 1.63 | 8.5 | -5.5% | +0.49% | [-0.25%, +1.15%] |
| Naive RSI<30 (stop 10%, 10 sessions), HOLDOUT 2023+ | 501 | 54% | +2.18% | +0.48% | 1.75 | 9.1 | -5.5% | +1.09% | [-0.15%, +2.47%] |
| Current trend rule (stop 10%, 10 sessions), HOLDOUT 2023+ | 783 | 49% | +0.38% | -0.23% | 1.12 | 9.1 | -5.5% | -0.19% | [-0.91%, +0.56%] |

## When SPY is below its 50-day average (today's regime), and 2022

### Universe B, chosen config, all years

| set | n | win | mean | median | PF | hold | avg MAE | mean vs SPY | 95% CI vs SPY (date-clustered) |
|---|---|---|---|---|---|---|---|---|---|
| SPY below SMA50 at signal | 236 | 66% | +2.14% | +7.78% | 1.76 | 7.9 | -6.5% | +1.09% | [-0.01%, +2.19%] |
| SPY above SMA50 at signal | 192 | 62% | +1.23% | +3.94% | 1.39 | 8.9 | -5.7% | +0.77% | [-0.44%, +1.87%] |
| SPY below SMA50 at signal (trend rule) | 20790 | 53% | -0.05% | +0.40% | 0.98 | 9.5 | -4.5% | -0.36% | [-0.47%, -0.25%] |
| SPY above SMA50 (trend rule) | 74805 | 52% | +0.15% | +0.16% | 1.08 | 9.8 | -3.7% | -0.19% | [-0.25%, -0.14%] |
| GFC (Sep 2008 - Mar 2009) | 0 | | | | | | | | |
| Aug - Dec 2011 | 1 | 100% | +7.78% | +7.78% | inf | 10.0 | -7.0% | +12.92% | - |
| 2018 Q4 | 4 | 75% | -0.69% | +7.78% | 0.90 | 3.0 | -9.5% | +0.17% | - |
| Feb - Jun 2020 | 3 | 33% | -1.89% | -5.69% | 0.58 | 2.3 | -6.7% | -0.13% | - |
| 2022 | 12 | 50% | +0.28% | +3.26% | 1.07 | 6.9 | -9.2% | +1.02% | - |
| Mar - Jun 2025 | 16 | 56% | +0.13% | +2.40% | 1.03 | 7.4 | -9.7% | +1.23% | - |
| GFC (Sep 2008 - Mar 2009) (trend rule) | 1007 | 30% | -3.58% | -6.45% | 0.37 | 7.8 | -8.7% | -0.22% | - |
| Aug - Dec 2011 (trend rule) | 1308 | 54% | -0.05% | +0.58% | 0.98 | 9.6 | -4.5% | -0.29% | - |
| 2018 Q4 (trend rule) | 753 | 28% | -3.58% | -3.69% | 0.21 | 9.2 | -6.5% | -0.47% | - |
| Feb - Jun 2020 (trend rule) | 1651 | 47% | -0.31% | -0.52% | 0.92 | 8.8 | -6.3% | -0.19% | - |
| 2022 (trend rule) | 3840 | 40% | -1.61% | -1.67% | 0.52 | 9.4 | -5.4% | +0.07% | - |
| Mar - Jun 2025 (trend rule) | 1447 | 56% | +0.63% | +0.65% | 1.37 | 9.7 | -3.8% | -0.49% | - |

### Universe A, chosen config, all years

| set | n | win | mean | median | PF | hold | avg MAE | mean vs SPY | 95% CI vs SPY (date-clustered) |
|---|---|---|---|---|---|---|---|---|---|
| SPY below SMA50 at signal | 27 | 78% | +3.15% | +7.78% | 2.19 | 5.8 | -7.1% | +2.86% | [+0.38%, +5.25%] |
| SPY above SMA50 at signal | 17 | 71% | +4.66% | +7.78% | 3.87 | 8.1 | -7.6% | +3.50% | [+0.36%, +6.84%] |
| SPY below SMA50 at signal (trend rule) | 835 | 50% | +0.06% | +0.03% | 1.02 | 9.3 | -5.0% | -0.21% | [-0.62%, +0.23%] |
| SPY above SMA50 (trend rule) | 2948 | 51% | +0.46% | +0.19% | 1.21 | 9.6 | -4.2% | +0.12% | [-0.14%, +0.38%] |
| GFC (Sep 2008 - Mar 2009) | 0 | | | | | | | | |
| Aug - Dec 2011 | 0 | | | | | | | | |
| 2018 Q4 | 1 | 100% | +7.78% | +7.78% | inf | 1.0 | -0.5% | +9.56% | - |
| Feb - Jun 2020 | 0 | | | | | | | | |
| 2022 | 2 | 50% | -2.12% | -2.12% | 0.65 | 4.0 | -11.6% | -2.53% | - |
| Mar - Jun 2025 | 5 | 80% | +4.96% | +7.78% | 4.91 | 5.2 | -8.8% | +4.88% | - |
| GFC (Sep 2008 - Mar 2009) (trend rule) | 41 | 41% | -0.48% | -2.84% | 0.89 | 8.6 | -7.2% | +2.08% | - |
| Aug - Dec 2011 (trend rule) | 55 | 58% | -0.12% | +0.63% | 0.94 | 9.7 | -4.5% | -0.41% | - |
| 2018 Q4 (trend rule) | 33 | 42% | -1.34% | -1.40% | 0.58 | 9.3 | -5.9% | +0.69% | - |
| Feb - Jun 2020 (trend rule) | 61 | 51% | +0.05% | +0.32% | 1.02 | 9.0 | -5.4% | +0.63% | - |
| 2022 (trend rule) | 131 | 34% | -1.56% | -1.87% | 0.58 | 9.2 | -6.0% | -0.46% | - |
| Mar - Jun 2025 (trend rule) | 52 | 60% | +2.49% | +1.40% | 2.04 | 9.2 | -5.2% | +2.01% | - |

## By year, chosen config

| year | B n | B mean | B median | B vs SPY | A n | A mean | A median | A vs SPY |
|---|---|---|---|---|---|---|---|---|
| 2003 | 9 | +3.49% | +7.78% | +1.39% | 2 | +7.78% | +7.78% | +8.70% |
| 2004 | 13 | +4.22% | +7.78% | +3.89% | 2 | +1.66% | +1.66% | -0.01% |
| 2005 | 13 | +1.21% | +7.78% | +0.20% | 2 | -0.35% | -0.35% | +0.23% |
| 2006 | 16 | +0.31% | +1.26% | +0.02% | 2 | -1.79% | -1.79% | -2.32% |
| 2007 | 10 | +2.63% | +6.35% | +1.79% | 0 | - | - | - |
| 2009 | 23 | +4.79% | +7.78% | +2.13% | 1 | +7.78% | +7.78% | +5.94% |
| 2010 | 6 | -0.70% | +1.27% | -1.42% | 1 | +7.78% | +7.78% | +4.97% |
| 2011 | 17 | +4.39% | +7.78% | +4.30% | 1 | +7.78% | +7.78% | +6.22% |
| 2012 | 22 | -0.76% | -0.71% | -0.75% | 0 | - | - | - |
| 2013 | 6 | +7.16% | +7.78% | +7.16% | 0 | - | - | - |
| 2014 | 12 | +1.91% | +7.78% | +1.61% | 0 | - | - | - |
| 2015 | 2 | +5.25% | +5.25% | +5.80% | 0 | - | - | - |
| 2016 | 5 | +3.12% | +6.08% | +3.62% | 0 | - | - | - |
| 2017 | 9 | +4.91% | +7.78% | +4.70% | 0 | - | - | - |
| 2018 | 17 | +0.92% | +3.06% | +0.27% | 1 | +7.78% | +7.78% | +9.56% |
| 2019 | 12 | -0.99% | -0.08% | -0.56% | 0 | - | - | - |
| 2020 | 9 | +4.00% | +7.78% | +2.83% | 0 | - | - | - |
| 2021 | 49 | +2.32% | +5.29% | +0.40% | 3 | -5.15% | -4.80% | -6.42% |
| 2022 | 12 | +0.28% | +3.26% | +1.02% | 2 | -2.12% | -2.12% | -2.53% |
| 2023 | 19 | +2.57% | +5.72% | +0.96% | 3 | +7.78% | +7.78% | +6.04% |
| 2024 | 40 | +1.77% | +7.78% | +0.83% | 8 | +4.63% | +7.78% | +4.55% |
| 2025 | 49 | +0.10% | +2.86% | +0.06% | 14 | +4.36% | +7.78% | +3.85% |
| 2026 | 58 | +0.46% | +7.78% | -0.35% | 2 | +8.34% | +8.34% | +6.32% |

## Portfolio simulation

Max 5 concurrent positions, 5% of current equity each, lowest RSI first, $100k start, a position's slot is freed the day after it exits.

| universe | period | strategy | CAGR | max drawdown | exposure | trades | end equity |
|---|---|---|---|---|---|---|---|
| B | 2001- | Chosen rebound | +1.2% | -6.7% | 2% | 375 | $137,040 |
| B | 2001- | Current trend rule | +1.1% | -25.6% | 22% | 3360 | $131,266 |
| B | 2001- | SPY buy & hold | +9.0% | -55.2% | 100% | 0 | $922,314 |
| B | 2023- | Chosen rebound | +1.3% | -6.7% | 5% | 134 | $104,929 |
| B | 2023- | Current trend rule | +4.1% | -7.9% | 22% | 490 | $116,881 |
| B | 2023- | SPY buy & hold | +20.7% | -18.8% | 100% | 0 | $206,594 |
| A | 2001- | Chosen rebound | +0.3% | -1.7% | 0% | 44 | $108,519 |
| A | 2001- | Current trend rule | +0.5% | -25.2% | 18% | 2692 | $114,697 |
| A | 2001- | SPY buy & hold | +9.0% | -55.2% | 100% | 0 | $922,314 |
| A | 2023- | Chosen rebound | +1.8% | -1.7% | 1% | 27 | $107,125 |
| A | 2023- | Current trend rule | -1.4% | -12.7% | 20% | 478 | $94,611 |
| A | 2023- | SPY buy & hold | +20.7% | -18.8% | 100% | 0 | $206,594 |
