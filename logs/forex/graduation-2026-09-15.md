# Forex Graduation Report — 2026-09-15

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 12 | ❌ |
| 🎯 Win rate | 55% | 75% | ✅ |
| 💰 Profit factor | 1.50 | 2.98 | ✅ |
| 📈 Expectancy/trade | >$0 | $432.00 | ✅ |
| 🏦 Net return | 3.0% | 5.18% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 12 |
| Wins / Losses | 9W / 3L |
| Win rate | 75% |
| Gross wins | $7,807.07 |
| Gross losses | $2,623.10 |
| Profit factor | 2.98 |
| Avg win | $867.45 |
| Avg loss | $874.37 |
| Expectancy/trade | $432.00 |
| Total realized P&L | $+5,183.97 |
| Net return | 5.18% |

## Blockers to Graduation
- Need **3 more closed trades** (have 12, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| GBP_JPY | 1 | 0 | $+1707.83 | +122.0 | ✅ Active |
| EUR_JPY | 2 | 0 | $+1323.31 | +161.9 | ✅ Active |
| USD_CAD | 1 | 0 | $+952.38 | +29.2 | ✅ Active |
| CAD_JPY | 1 | 0 | $+650.91 | +46.3 | ✅ Active |
| AUD_JPY | 1 | 0 | $+130.46 | +7.0 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| NZD_USD | 1 | 1 | $-1429.09 | -27.9 | ✅ Active |

## What the Agent Learned (4 adjustments)
- ⬇️ **carry** weight: 0.485 → 0.5 — avg score 0.60 in wins vs 0.80 in losses — not predictive (delta=-0.20)
- ⬇️ **momentum** weight: 0.485 → 0.5 — avg score 0.49 in wins vs 0.80 in losses — not predictive (delta=-0.31)
- ⬆️ **technical** weight: 7.953 → 8.253 — avg score 0.56 in wins vs 0.39 in losses (+0.17 predictive edge)
- ⬇️ **risk_reward** weight: 0.485 → 0.5 — avg score 0.68 in wins vs 0.85 in losses — not predictive (delta=-0.17)

## Direction Bias
- Long win rate:  0.62
- Short win rate: 1.0
- Sample: 12 trades

## Updated Judge Weights
```json
{
  "carry": 0.483,
  "momentum": 0.483,
  "technical": 7.979,
  "session": 0.285,
  "volatility": 0.285,
  "risk_reward": 0.483
}
```

## Estimated Path to Graduation
- Trades needed: **3** more  (~2 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.