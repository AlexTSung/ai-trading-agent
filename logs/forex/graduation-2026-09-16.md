# Forex Graduation Report — 2026-09-16

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 14 | ❌ |
| 🎯 Win rate | 55% | 79% | ✅ |
| 💰 Profit factor | 1.50 | 3.55 | ✅ |
| 📈 Expectancy/trade | >$0 | $476.89 | ✅ |
| 🏦 Net return | 3.0% | 6.68% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 14 |
| Wins / Losses | 11W / 3L |
| Win rate | 79% |
| Gross wins | $9,299.61 |
| Gross losses | $2,623.10 |
| Profit factor | 3.55 |
| Avg win | $845.42 |
| Avg loss | $874.37 |
| Expectancy/trade | $476.89 |
| Total realized P&L | $+6,676.51 |
| Net return | 6.68% |

## Blockers to Graduation
- Need **1 more closed trades** (have 14, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 3 | 0 | $+1668.72 | +175.8 | ✅ Active |
| GBP_USD | 1 | 0 | $+1147.12 | +49.1 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| CAD_JPY | 1 | 0 | $+650.91 | +46.3 | ✅ Active |
| AUD_JPY | 1 | 0 | $+130.46 | +7.0 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| NZD_USD | 1 | 1 | $-1429.09 | -27.9 | ✅ Active |

## What the Agent Learned (4 adjustments)
- ⬇️ **carry** weight: 0.483 → 0.5 — avg score 0.61 in wins vs 0.80 in losses — not predictive (delta=-0.19)
- ⬇️ **momentum** weight: 0.483 → 0.5 — avg score 0.49 in wins vs 0.80 in losses — not predictive (delta=-0.31)
- ⬆️ **technical** weight: 8.054 → 8.354 — avg score 0.61 in wins vs 0.39 in losses (+0.22 predictive edge)
- ⬇️ **risk_reward** weight: 0.483 → 0.5 — avg score 0.71 in wins vs 0.85 in losses — not predictive (delta=-0.14)

## Direction Bias
- Long win rate:  0.67
- Short win rate: 1.0
- Sample: 14 trades

## Updated Judge Weights
```json
{
  "carry": 0.483,
  "momentum": 0.483,
  "technical": 8.071,
  "session": 0.24,
  "volatility": 0.24,
  "risk_reward": 0.483
}
```

## Estimated Path to Graduation
- Trades needed: **1** more  (~1 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.