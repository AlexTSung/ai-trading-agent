# Graduation Decision — 2026-10-27

**Baseline**: 2026-08-20 (Phase 2B)
**Trades counted**: 8  ·  4 excluded as DATA_SUSPECT
**Honest equity**: unavailable  (baseline $91,455.93)

## Criteria

| Criterion | Required | Actual | |
|---|---|---|---|
| Occurrences (scored buys) | ≥15 | 8 | ❌ |
| Closed round trips | ≥10 | 6 | ❌ |
| Net return | ≥+3.0% | unavailable | ❌ |
| Profit factor (realized) | ≥1.5 | 11.57 | ✅ |
| Expectancy / round trip | >0.0% | +6.24% | ✅ |
| Realized win rate | ≥55.0% | 83.3% | ✅ |
| Reviewer win rate | ≥55.0% | 50.0% | ❌ |
| Avg Judge score | ≥7.0 | 8.63 | ✅ |
| Avg Reviewer score | ≥6.0 | 8.31 | ✅ |
| Beat SPY | agent > SPY | unavailable | ❌ |

## Verdict

**DO NOT GRADUATE.** Phase 3 stays locked.

Failing: Occurrences (scored buys), Closed round trips, Net return, Reviewer win rate, Beat SPY.

Only 8 qualifying trade(s) since the baseline. Note that reviewer.py scores buys only, so a period of net selling produces no calibration rows at all.

## Excluded as DATA_SUSPECT

- 2026-09-24 WAB: P&L -100.0% — implausible, likely an unadjusted corporate action
- 2026-09-24 AMZN: P&L -100.0% — implausible, likely an unadjusted corporate action
- 2026-10-01 DIS: P&L -100.0% — implausible, likely an unadjusted corporate action
- 2026-10-12 MRK: P&L -100.0% — implausible, likely an unadjusted corporate action

## Closed round trips

Net realized: **$+1,764.80** over 6 trades  ·  avg win +8.19%  ·  avg loss -3.54%  ·  avg hold 48d

| Exit | Symbol | Qty | Entry | Exit | P&L $ | P&L % | Held |
|---|---|---|---|---|---|---|---|
| 2026-08-26 | NVDA | 25 | 192.81 | 210.28 | +436.75 | +9.06% | 58d |
| 2026-09-02 | DDOG | 21 | 218.57 | 210.84 | -162.33 | -3.54% | 76d |
| 2026-09-30 | AAPL | 17 | 283.70 | 336.17 | +891.99 | +18.49% | 93d |
| 2026-10-21 | CEG | 17 | 263.21 | 271.31 | +137.68 | +3.08% | 30d |
| 2026-10-21 | CEG | 17 | 255.18 | 271.31 | +274.19 | +6.32% | 19d |
| 2026-10-22 | PYPL | 84 | 55.17 | 57.39 | +186.52 | +4.02% | 15d |

Full history: `logs/paper/round-trips.csv`

## What these criteria do and do not prove

Occurrences, win rate and the two agent scores measure *process*. Net return, profit factor, expectancy and the SPY comparison measure *money*, and they are measured on closed round trips rather than entry-day drift. Both sets must pass: a high win rate with small gains and large losses is a losing system, and an agent that trails SPY is worse than an index fund no matter how disciplined it looks.
# Calibration Report — Last 30 Days
**Trades analyzed**: 6 (8 logged, 2 excluded as DATA_SUSPECT)
**Win rate**: 50.0% (3 good, 0 poor) — graduation requires ≥55%
**Avg Judge→Reviewer delta**: -0.96

## Interpretation
⚠️ **2 trade(s) excluded as DATA_SUSPECT**: DIS, MRK. Implausible P&L indicates an unadjusted corporate action rather than a real outcome. Reconcile with the broker before treating these as performance.
Judge calibration is **well-tuned** (delta within ±1.0)

## Per-Symbol Bias
- **ARGX**: avg delta +1.48 over 1 trades
- **CEG**: avg delta +1.13 over 1 trades
- **DIS**: avg delta -3.50 over 1 trades
- **MDT**: avg delta -2.12 over 1 trades
- **MRK**: avg delta -3.85 over 1 trades
- **PYPL**: avg delta -1.02 over 1 trades
- **TGT**: avg delta -0.12 over 1 trades
- **VZ**: avg delta +0.35 over 1 trades
{
  "phantom_equity": [
    {
      "symbol": "LBGJ",
      "qty": 154903.0,
      "avg_entry_price": 0.0288,
      "current_price": 1.47,
      "price_ratio": 51.04,
      "unrealized_plpc": 50.04167,
      "reported_market_value": 227707.41,
      "cost_basis": 4461.21,
      "phantom_amount": 223246.2,
      "reasons": [
        "price $1.4700 is 51x its cost basis $0.0288",
        "unrealized P&L 5,004% exceeds the 1,000% plausibility ceiling",
        "entry price $0.0288 was below the $5.00 floor, where reverse splits are routine"
      ],
      "action": "TRADING_BLOCKED \u2014 verify the corporate action with the broker before buying or selling this symbol"
    }
  ],
  "total_phantom_amount": 223246.2,
  "verdict": "SUSPECT \u2014 reconcile with the broker"
}
