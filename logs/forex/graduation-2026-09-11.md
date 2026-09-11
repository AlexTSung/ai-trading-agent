# Forex Graduation Report — 2026-09-11

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 10 | ❌ |
| 🎯 Win rate | 55% | 80% | ✅ |
| 💰 Profit factor | 1.50 | 13.95 | ✅ |
| 📈 Expectancy/trade | >$0 | $712.63 | ✅ |
| 🏦 Net return | 3.0% | 7.13% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 10 |
| Wins / Losses | 8W / 2L |
| Win rate | 80% |
| Gross wins | $7,676.61 |
| Gross losses | $550.34 |
| Profit factor | 13.95 |
| Avg win | $959.58 |
| Avg loss | $275.17 |
| Expectancy/trade | $712.63 |
| Total realized P&L | $+7,126.27 |
| Net return | 7.13% |

## Blockers to Graduation
- Need **5 more closed trades** (have 10, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 2 | 0 | $+1323.31 | +161.9 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| CAD_JPY | 1 | 0 | $+650.91 | +46.3 | ✅ Active |
| NZD_USD | 1 | 0 | $+643.67 | +22.7 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |

## What the Agent Learned (4 adjustments)
- ⬇️ **carry** weight: 0.483 → 0.5 — avg score 0.55 in wins vs 1.00 in losses — not predictive (delta=-0.45)
- ⬇️ **momentum** weight: 0.483 → 0.5 — avg score 0.53 in wins vs 1.00 in losses — not predictive (delta=-0.47)
- ⬆️ **technical** weight: 7.685 → 7.985 — avg score 0.52 in wins vs 0.17 in losses (+0.35 predictive edge)
- ⬇️ **risk_reward** weight: 0.483 → 0.5 — avg score 0.66 in wins vs 0.85 in losses — not predictive (delta=-0.19)

## Direction Bias
- Long win rate:  0.67
- Short win rate: 1.0
- Sample: 10 trades

## Updated Judge Weights
```json
{
  "carry": 0.483,
  "momentum": 0.483,
  "technical": 7.714,
  "session": 0.418,
  "volatility": 0.418,
  "risk_reward": 0.483
}
```

## Estimated Path to Graduation
- Trades needed: **5** more  (~4 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.