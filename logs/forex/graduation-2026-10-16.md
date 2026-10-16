# Forex Graduation Report — 2026-10-16

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 9 | ❌ |
| 🎯 Win rate | 55% | 56% | ✅ |
| 💰 Profit factor | 1.50 | 0.95 | ❌ |
| 📈 Expectancy/trade | >$0 | $-26.22 | ❌ |
| 🏦 Net return | 3.0% | -0.24% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 9 |
| Wins / Losses | 5W / 4L |
| Win rate | 56% |
| Gross wins | $4,239.95 |
| Gross losses | $4,475.93 |
| Profit factor | 0.95 |
| Avg win | $847.99 |
| Avg loss | $1118.98 |
| Expectancy/trade | $-26.22 |
| Total realized P&L | $-235.98 |
| Net return | -0.24% |

## Blockers to Graduation
- Need **6 more closed trades** (have 9, need 15)
- Need **+3.24% more return** (have -0.24%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| AUD_USD | 1 | 0 | $+1519.75 | +42.4 | ✅ Active |
| CAD_JPY | 1 | 0 | $+310.83 | +21.4 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| EUR_USD | 0 | 1 | $-1695.00 | -33.9 | ✅ Active |
| USD_CAD | 0 | 1 | $-2230.59 | -78.9 | ✅ Active |

## What the Agent Learned (5 adjustments)
- ⬇️ **carry** weight: 0.5 → 0.5 — avg score 0.44 in wins vs 0.60 in losses — not predictive (delta=-0.16)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.48 in wins vs 0.85 in losses — not predictive (delta=-0.37)
- ⬇️ **session** weight: 0.5 → 0.5 — avg score 0.55 in wins vs 0.81 in losses — not predictive (delta=-0.26)
- ⬇️ **volatility** weight: 0.5 → 0.5 — avg score 0.36 in wins vs 0.66 in losses — not predictive (delta=-0.30)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.54 in wins vs 0.85 in losses — not predictive (delta=-0.31)

## Direction Bias
- Long win rate:  0.5
- Short win rate: 1.0
- Sample: 9 trades

## Updated Judge Weights
```json
{
  "carry": 0.5,
  "momentum": 0.5,
  "technical": 7.5,
  "session": 0.5,
  "volatility": 0.5,
  "risk_reward": 0.5
}
```

## Estimated Path to Graduation
- Trades needed: **6** more  (~4 weeks at 1-2/week)
- Return needed: **+3.24%** more  (~$3,236 more profit needed)
- Manual approval required before any real money is deployed.