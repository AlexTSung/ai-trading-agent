# Forex Graduation Report — 2026-10-15

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 8 | ❌ |
| 🎯 Win rate | 55% | 50% | ❌ |
| 💰 Profit factor | 1.50 | 0.88 | ❌ |
| 📈 Expectancy/trade | >$0 | $-68.35 | ❌ |
| 🏦 Net return | 3.0% | -0.55% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 8 |
| Wins / Losses | 4W / 4L |
| Win rate | 50% |
| Gross wins | $3,929.12 |
| Gross losses | $4,475.93 |
| Profit factor | 0.88 |
| Avg win | $982.28 |
| Avg loss | $1118.98 |
| Expectancy/trade | $-68.35 |
| Total realized P&L | $-546.81 |
| Net return | -0.55% |

## Blockers to Graduation
- Need **7 more closed trades** (have 8, need 15)
- Need **+3.55% more return** (have -0.55%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| AUD_USD | 1 | 0 | $+1519.75 | +42.4 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| EUR_USD | 0 | 1 | $-1695.00 | -33.9 | ✅ Active |
| USD_CAD | 0 | 1 | $-2230.59 | -78.9 | ✅ Active |

## What the Agent Learned (5 adjustments)
- ⬇️ **carry** weight: 0.5 → 0.5 — avg score 0.30 in wins vs 0.60 in losses — not predictive (delta=-0.30)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.40 in wins vs 0.85 in losses — not predictive (delta=-0.45)
- ⬇️ **session** weight: 0.5 → 0.5 — avg score 0.44 in wins vs 0.81 in losses — not predictive (delta=-0.38)
- ⬇️ **volatility** weight: 0.5 → 0.5 — avg score 0.31 in wins vs 0.66 in losses — not predictive (delta=-0.35)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.46 in wins vs 0.85 in losses — not predictive (delta=-0.39)

## Direction Bias
- Long win rate:  0.43
- Short win rate: 1.0
- Sample: 8 trades

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
- Trades needed: **7** more  (~5 weeks at 1-2/week)
- Return needed: **+3.55%** more  (~$3,547 more profit needed)
- Manual approval required before any real money is deployed.