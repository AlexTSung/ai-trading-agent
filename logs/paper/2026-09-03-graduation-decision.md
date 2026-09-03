# Graduation Decision — 2026-09-03

**Baseline**: 2026-08-20 (Phase 2B)
**Trades counted**: 4
**Honest equity**: unavailable  (baseline $91,455.93)

## Criteria

| Criterion | Required | Actual | |
|---|---|---|---|
| Occurrences (scored buys) | ≥15 | 4 | ❌ |
| Closed round trips | ≥10 | 4 | ❌ |
| Net return | ≥+3.0% | unavailable | ❌ |
| Profit factor (realized) | ≥1.5 | 0.16 | ❌ |
| Expectancy / round trip | >0.0% | -11.73% | ❌ |
| Realized win rate | ≥55.0% | 25.0% | ❌ |
| Reviewer win rate | ≥55.0% | 0.0% | ❌ |
| Avg Judge score | ≥7.0 | 0.00 | ❌ |
| Avg Reviewer score | ≥6.0 | 6.00 | ✅ |
| Beat SPY | agent > SPY | unavailable | ❌ |

## Verdict

**DO NOT GRADUATE.** Phase 3 stays locked.

Failing: Occurrences (scored buys), Closed round trips, Net return, Profit factor (realized), Expectancy / round trip, Realized win rate, Reviewer win rate, Avg Judge score, Beat SPY.

Only 4 qualifying trade(s) since the baseline. Note that reviewer.py scores buys only, so a period of net selling produces no calibration rows at all.

## Closed round trips

Net realized: **$+272.77** over 4 trades  ·  avg win +9.06%  ·  avg loss -18.66%  ·  avg hold 34d

| Exit | Symbol | Qty | Entry | Exit | P&L $ | P&L % | Held |
|---|---|---|---|---|---|---|---|
| 2026-08-26 | NVDA | 25 | 192.81 | 210.28 | +436.75 | +9.06% | 58d |
| 2026-09-02 | DDOG | 21 | 218.57 | 210.84 | -162.33 | -3.54% | 76d |
| 2026-09-02 | NVDA261016P00200000 | 1 | 2.79 | 2.46 | -0.33 | -11.83% | 0d |
| 2026-09-03 | MSFT261016P00450000 | 1 | 3.25 | 1.93 | -1.32 | -40.62% | 1d |

Full history: `logs/paper/round-trips.csv`

## What these criteria do and do not prove

Occurrences, win rate and the two agent scores measure *process*. Net return, profit factor, expectancy and the SPY comparison measure *money*, and they are measured on closed round trips rather than entry-day drift. Both sets must pass: a high win rate with small gains and large losses is a losing system, and an agent that trails SPY is worse than an index fund no matter how disciplined it looks.
No calibration data in the last 30 days.
{
  "phantom_equity": [
    {
      "symbol": "LBGJ",
      "qty": 154903.0,
      "avg_entry_price": 0.0288,
      "current_price": 2.41,
      "price_ratio": 83.68,
      "unrealized_plpc": 82.68056,
      "reported_market_value": 373316.23,
      "cost_basis": 4461.21,
      "phantom_amount": 368855.02,
      "reasons": [
        "price $2.4100 is 84x its cost basis $0.0288",
        "unrealized P&L 8,268% exceeds the 1,000% plausibility ceiling",
        "entry price $0.0288 was below the $5.00 floor, where reverse splits are routine"
      ],
      "action": "TRADING_BLOCKED \u2014 verify the corporate action with the broker before buying or selling this symbol"
    }
  ],
  "total_phantom_amount": 368855.02,
  "verdict": "SUSPECT \u2014 reconcile with the broker"
}
