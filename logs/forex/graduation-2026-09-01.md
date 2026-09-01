# Forex Graduation Report — 2026-09-01

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 5 | ❌ |
| 🎯 Win rate | 55% | 60% | ✅ |
| 💰 Profit factor | 1.50 | 4.38 | ✅ |
| 📈 Expectancy/trade | >$0 | $371.81 | ✅ |
| 🏦 Net return | 3.0% | 1.86% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 5 |
| Wins / Losses | 3W / 2L |
| Win rate | 60% |
| Gross wins | $2,409.37 |
| Gross losses | $550.34 |
| Profit factor | 4.38 |
| Avg win | $803.12 |
| Avg loss | $275.17 |
| Expectancy/trade | $371.81 |
| Total realized P&L | $+1,859.03 |
| Net return | 1.86% |

## Blockers to Graduation
- Need **10 more closed trades** (have 5, need 15)
- Need **+1.14% more return** (have 1.86%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |

## What the Agent Learned (5 adjustments)
- ⬇️ **carry** weight: 1.533 → 1.38 — avg score 0.33 in wins vs 1.00 in losses — not predictive (delta=-0.67)
- ⬇️ **momentum** weight: 2.226 → 2.026 — avg score 0.33 in wins vs 1.00 in losses — not predictive (delta=-0.67)
- ⬇️ **session** weight: 0.765 → 0.689 — avg score 0.33 in wins vs 0.75 in losses — not predictive (delta=-0.42)
- ⬇️ **volatility** weight: 1.15 → 1.035 — avg score 0.23 in wins vs 0.70 in losses — not predictive (delta=-0.47)
- ⬇️ **risk_reward** weight: 0.765 → 0.689 — avg score 0.33 in wins vs 0.85 in losses — not predictive (delta=-0.52)

## Direction Bias
- Long win rate:  0.6
- Short win rate: None
- Sample: 5 trades

## Updated Judge Weights
```json
{
  "carry": 1.471,
  "momentum": 2.16,
  "technical": 3.796,
  "session": 0.735,
  "volatility": 1.103,
  "risk_reward": 0.735
}
```

## Estimated Path to Graduation
- Trades needed: **10** more  (~7 weeks at 1-2/week)
- Return needed: **+1.14%** more  (~$1,141 more profit needed)
- Manual approval required before any real money is deployed.