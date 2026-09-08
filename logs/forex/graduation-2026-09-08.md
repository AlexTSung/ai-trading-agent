# Forex Graduation Report — 2026-09-08

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 6 | ❌ |
| 🎯 Win rate | 55% | 67% | ✅ |
| 💰 Profit factor | 1.50 | 7.48 | ✅ |
| 📈 Expectancy/trade | >$0 | $594.48 | ✅ |
| 🏦 Net return | 3.0% | 3.57% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 6 |
| Wins / Losses | 4W / 2L |
| Win rate | 67% |
| Gross wins | $4,117.20 |
| Gross losses | $550.34 |
| Profit factor | 7.48 |
| Avg win | $1029.30 |
| Avg loss | $275.17 |
| Expectancy/trade | $594.48 |
| Total realized P&L | $+3,566.86 |
| Net return | 3.57% |

## Blockers to Graduation
- Need **9 more closed trades** (have 6, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |

## What the Agent Learned (5 adjustments)
- ⬇️ **carry** weight: 0.5 → 0.5 — avg score 0.45 in wins vs 1.00 in losses — not predictive (delta=-0.55)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.45 in wins vs 1.00 in losses — not predictive (delta=-0.55)
- ⬇️ **session** weight: 0.5 → 0.5 — avg score 0.44 in wins vs 0.75 in losses — not predictive (delta=-0.31)
- ⬇️ **volatility** weight: 0.5 → 0.5 — avg score 0.42 in wins vs 0.70 in losses — not predictive (delta=-0.27)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.46 in wins vs 0.85 in losses — not predictive (delta=-0.39)

## Direction Bias
- Long win rate:  0.6
- Short win rate: 1.0
- Sample: 6 trades

## Updated Judge Weights
```json
{
  "carry": 0.5,
  "momentum": 0.5,
  "technical": 7.5,
  "session": 0.5,
  "volatility": 0.5,
  "risk_reward": 0.5
}
```

## Estimated Path to Graduation
- Trades needed: **9** more  (~6 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.