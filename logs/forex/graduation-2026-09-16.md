# Forex Graduation Report — 2026-09-16

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 13 | ❌ |
| 🎯 Win rate | 55% | 77% | ✅ |
| 💰 Profit factor | 1.50 | 3.11 | ✅ |
| 📈 Expectancy/trade | >$0 | $425.34 | ✅ |
| 🏦 Net return | 3.0% | 5.53% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 13 |
| Wins / Losses | 10W / 3L |
| Win rate | 77% |
| Gross wins | $8,152.49 |
| Gross losses | $2,623.10 |
| Profit factor | 3.11 |
| Avg win | $815.25 |
| Avg loss | $874.37 |
| Expectancy/trade | $425.34 |
| Total realized P&L | $+5,529.39 |
| Net return | 5.53% |

## Blockers to Graduation
- Need **2 more closed trades** (have 13, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 3 | 0 | $+1668.72 | +175.8 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| CAD_JPY | 1 | 0 | $+650.91 | +46.3 | ✅ Active |
| AUD_JPY | 1 | 0 | $+130.46 | +7.0 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| NZD_USD | 1 | 1 | $-1429.09 | -27.9 | ✅ Active |

## What the Agent Learned (4 adjustments)
- ⬇️ **carry** weight: 0.483 → 0.5 — avg score 0.64 in wins vs 0.80 in losses — not predictive (delta=-0.16)
- ⬇️ **momentum** weight: 0.483 → 0.5 — avg score 0.46 in wins vs 0.80 in losses — not predictive (delta=-0.34)
- ⬆️ **technical** weight: 8.037 → 8.337 — avg score 0.58 in wins vs 0.39 in losses (+0.19 predictive edge)
- ⬇️ **risk_reward** weight: 0.483 → 0.5 — avg score 0.69 in wins vs 0.85 in losses — not predictive (delta=-0.16)

## Direction Bias
- Long win rate:  0.67
- Short win rate: 1.0
- Sample: 13 trades

## Updated Judge Weights
```json
{
  "carry": 0.483,
  "momentum": 0.483,
  "technical": 8.054,
  "session": 0.248,
  "volatility": 0.248,
  "risk_reward": 0.483
}
```

## Estimated Path to Graduation
- Trades needed: **2** more  (~2 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.