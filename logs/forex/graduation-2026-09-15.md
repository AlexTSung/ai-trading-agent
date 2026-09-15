# Forex Graduation Report — 2026-09-15

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 11 | ❌ |
| 🎯 Win rate | 55% | 73% | ✅ |
| 💰 Profit factor | 1.50 | 2.93 | ✅ |
| 📈 Expectancy/trade | >$0 | $459.41 | ✅ |
| 🏦 Net return | 3.0% | 5.05% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 11 |
| Wins / Losses | 8W / 3L |
| Win rate | 73% |
| Gross wins | $7,676.61 |
| Gross losses | $2,623.10 |
| Profit factor | 2.93 |
| Avg win | $959.58 |
| Avg loss | $874.37 |
| Expectancy/trade | $459.41 |
| Total realized P&L | $+5,053.51 |
| Net return | 5.05% |

## Blockers to Graduation
- Need **4 more closed trades** (have 11, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 2 | 0 | $+1323.31 | +161.9 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| CAD_JPY | 1 | 0 | $+650.91 | +46.3 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| NZD_USD | 1 | 1 | $-1429.09 | -27.9 | ✅ Active |

## What the Agent Learned (3 adjustments)
- ⬇️ **carry** weight: 0.5 → 0.5 — avg score 0.55 in wins vs 0.80 in losses — not predictive (delta=-0.25)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.53 in wins vs 0.80 in losses — not predictive (delta=-0.27)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.66 in wins vs 0.85 in losses — not predictive (delta=-0.19)

## Direction Bias
- Long win rate:  0.57
- Short win rate: 1.0
- Sample: 11 trades

## Updated Judge Weights
```json
{
  "carry": 0.5,
  "momentum": 0.5,
  "technical": 7.892,
  "session": 0.304,
  "volatility": 0.304,
  "risk_reward": 0.5
}
```

## Estimated Path to Graduation
- Trades needed: **4** more  (~3 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.