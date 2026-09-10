# Forex Graduation Report — 2026-09-10

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 9 | ❌ |
| 🎯 Win rate | 55% | 78% | ✅ |
| 💰 Profit factor | 1.50 | 12.77 | ✅ |
| 📈 Expectancy/trade | >$0 | $719.48 | ✅ |
| 🏦 Net return | 3.0% | 6.48% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 9 |
| Wins / Losses | 7W / 2L |
| Win rate | 78% |
| Gross wins | $7,025.70 |
| Gross losses | $550.34 |
| Profit factor | 12.77 |
| Avg win | $1003.67 |
| Avg loss | $275.17 |
| Expectancy/trade | $719.48 |
| Total realized P&L | $+6,475.36 |
| Net return | 6.48% |

## Blockers to Graduation
- Need **6 more closed trades** (have 9, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 2 | 0 | $+1323.31 | +161.9 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| NZD_USD | 1 | 0 | $+643.67 | +22.7 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |

## What the Agent Learned (6 adjustments)
- ⬇️ **carry** weight: 0.481 → 0.5 — avg score 0.49 in wins vs 1.00 in losses — not predictive (delta=-0.51)
- ⬇️ **momentum** weight: 0.481 → 0.5 — avg score 0.57 in wins vs 1.00 in losses — not predictive (delta=-0.43)
- ⬆️ **technical** weight: 7.595 → 7.895 — avg score 0.50 in wins vs 0.17 in losses (+0.33 predictive edge)
- ⬇️ **session** weight: 0.481 → 0.5 — avg score 0.61 in wins vs 0.75 in losses — not predictive (delta=-0.14)
- ⬇️ **volatility** weight: 0.481 → 0.5 — avg score 0.59 in wins vs 0.70 in losses — not predictive (delta=-0.11)
- ⬇️ **risk_reward** weight: 0.481 → 0.5 — avg score 0.63 in wins vs 0.85 in losses — not predictive (delta=-0.22)

## Direction Bias
- Long win rate:  0.6
- Short win rate: 1.0
- Sample: 9 trades

## Updated Judge Weights
```json
{
  "carry": 0.481,
  "momentum": 0.481,
  "technical": 7.595,
  "session": 0.481,
  "volatility": 0.481,
  "risk_reward": 0.481
}
```

## Estimated Path to Graduation
- Trades needed: **6** more  (~4 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.