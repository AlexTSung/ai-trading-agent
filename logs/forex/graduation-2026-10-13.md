# Forex Graduation Report — 2026-10-13

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 6 | ❌ |
| 🎯 Win rate | 55% | 67% | ✅ |
| 💰 Profit factor | 1.50 | 7.14 | ✅ |
| 📈 Expectancy/trade | >$0 | $563.13 | ✅ |
| 🏦 Net return | 3.0% | 3.38% | ✅ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 6 |
| Wins / Losses | 4W / 2L |
| Win rate | 67% |
| Gross wins | $3,929.12 |
| Gross losses | $550.34 |
| Profit factor | 7.14 |
| Avg win | $982.28 |
| Avg loss | $275.17 |
| Expectancy/trade | $563.13 |
| Total realized P&L | $+3,378.78 |
| Net return | 3.38% |

## Blockers to Graduation
- Need **9 more closed trades** (have 6, need 15)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| AUD_USD | 1 | 0 | $+1519.75 | +42.4 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |

## What the Agent Learned (6 adjustments)
- ⬇️ **carry** weight: 0.888 → 0.799 — avg score 0.30 in wins vs 1.00 in losses — not predictive (delta=-0.70)
- ⬇️ **momentum** weight: 0.646 → 0.581 — avg score 0.40 in wins vs 1.00 in losses — not predictive (delta=-0.60)
- ⬆️ **technical** weight: 6.972 → 7.272 — avg score 0.33 in wins vs 0.17 in losses (+0.17 predictive edge)
- ⬇️ **session** weight: 0.498 → 0.5 — avg score 0.44 in wins vs 0.75 in losses — not predictive (delta=-0.31)
- ⬇️ **volatility** weight: 0.498 → 0.5 — avg score 0.31 in wins vs 0.70 in losses — not predictive (delta=-0.39)
- ⬇️ **risk_reward** weight: 0.498 → 0.5 — avg score 0.46 in wins vs 0.85 in losses — not predictive (delta=-0.39)

## Direction Bias
- Long win rate:  0.6
- Short win rate: 1.0
- Sample: 6 trades

## Updated Judge Weights
```json
{
  "carry": 0.787,
  "momentum": 0.572,
  "technical": 7.163,
  "session": 0.493,
  "volatility": 0.493,
  "risk_reward": 0.493
}
```

## Estimated Path to Graduation
- Trades needed: **9** more  (~6 weeks at 1-2/week)
- Return needed: **+0.00%** more  (done ✅)
- Manual approval required before any real money is deployed.