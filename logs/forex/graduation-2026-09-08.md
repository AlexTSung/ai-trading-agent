# Forex Graduation Report — 2026-09-08

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 8 | ❌ |
| 🎯 Win rate | 55% | 75% | ✅ |
| 💰 Profit factor | 1.50 | 11.60 | ✅ |
| 📈 Expectancy/trade | >$0 | $728.96 | ✅ |
| 🏦 Net return | 3.0% | 5.83% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 8 |
| Wins / Losses | 6W / 2L |
| Win rate | 75% |
| Gross wins | $6,382.02 |
| Gross losses | $550.34 |
| Profit factor | 11.60 |
| Avg win | $1063.67 |
| Avg loss | $275.17 |
| Expectancy/trade | $728.96 |
| Total realized P&L | $+5,831.68 |
| Net return | 5.83% |

## Blockers to Graduation
- Need **7 more closed trades** (have 8, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 2 | 0 | $+1323.31 | +161.9 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |

## What the Agent Learned (6 adjustments)
- ⬇️ **carry** weight: 0.5 → 0.5 — avg score 0.50 in wins vs 1.00 in losses — not predictive (delta=-0.50)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.53 in wins vs 1.00 in losses — not predictive (delta=-0.47)
- ⬆️ **technical** weight: 7.5 → 7.8 — avg score 0.44 in wins vs 0.17 in losses (+0.28 predictive edge)
- ⬇️ **session** weight: 0.5 → 0.5 — avg score 0.58 in wins vs 0.75 in losses — not predictive (delta=-0.17)
- ⬇️ **volatility** weight: 0.5 → 0.5 — avg score 0.57 in wins vs 0.70 in losses — not predictive (delta=-0.13)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.59 in wins vs 0.85 in losses — not predictive (delta=-0.26)

## Direction Bias
- Long win rate:  0.6
- Short win rate: 1.0
- Sample: 8 trades

## Updated Judge Weights
```json
{
  "carry": 0.485,
  "momentum": 0.485,
  "technical": 7.573,
  "session": 0.485,
  "volatility": 0.485,
  "risk_reward": 0.485
}
```

## Estimated Path to Graduation
- Trades needed: **7** more  (~5 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.