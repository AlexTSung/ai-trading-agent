# Forex Graduation Report — 2026-09-02

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 6 | ❌ |
| 🎯 Win rate | 55% | 50% | ❌ |
| 💰 Profit factor | 1.50 | 1.10 | ❌ |
| 📈 Expectancy/trade | >$0 | $38.00 | ✅ |
| 🏦 Net return | 3.0% | 0.23% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 6 |
| Wins / Losses | 3W / 3L |
| Win rate | 50% |
| Gross wins | $2,409.37 |
| Gross losses | $2,181.39 |
| Profit factor | 1.10 |
| Avg win | $803.12 |
| Avg loss | $727.13 |
| Expectancy/trade | $38.00 |
| Total realized P&L | $+227.97 |
| Net return | 0.23% |

## Blockers to Graduation
- Need **9 more closed trades** (have 6, need 15)
- Need **+2.77% more return** (have 0.23%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| CAD_JPY | 0 | 1 | $-1631.06 | -51.6 | ✅ Active |

## What the Agent Learned (6 adjustments)
- ⬇️ **carry** weight: 1.16 → 1.044 — avg score 0.33 in wins vs 1.00 in losses — not predictive (delta=-0.67)
- ⬇️ **momentum** weight: 1.729 → 1.556 — avg score 0.33 in wins vs 1.00 in losses — not predictive (delta=-0.67)
- ⬇️ **technical** weight: 5.075 → 4.875 — avg score 0.17 in wins vs 0.39 in losses — not predictive (delta=-0.22)
- ⬇️ **session** weight: 0.582 → 0.524 — avg score 0.33 in wins vs 0.75 in losses — not predictive (delta=-0.42)
- ⬇️ **volatility** weight: 0.871 → 0.784 — avg score 0.23 in wins vs 0.57 in losses — not predictive (delta=-0.33)
- ⬇️ **risk_reward** weight: 0.582 → 0.524 — avg score 0.33 in wins vs 0.85 in losses — not predictive (delta=-0.52)

## Direction Bias
- Long win rate:  0.5
- Short win rate: None
- Sample: 6 trades

## Updated Judge Weights
```json
{
  "carry": 1.122,
  "momentum": 1.672,
  "technical": 5.238,
  "session": 0.563,
  "volatility": 0.842,
  "risk_reward": 0.563
}
```

## Estimated Path to Graduation
- Trades needed: **9** more  (~6 weeks at 1-2/week)
- Return needed: **+2.77%** more  (~$2,772 more profit needed)
- Manual approval required before any real money is deployed.