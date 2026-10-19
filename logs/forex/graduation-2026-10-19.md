# Forex Graduation Report — 2026-10-19

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 10 | ❌ |
| 🎯 Win rate | 55% | 60% | ✅ |
| 💰 Profit factor | 1.50 | 1.09 | ❌ |
| 📈 Expectancy/trade | >$0 | $39.47 | ✅ |
| 🏦 Net return | 3.0% | 0.39% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 10 |
| Wins / Losses | 6W / 4L |
| Win rate | 60% |
| Gross wins | $4,870.62 |
| Gross losses | $4,475.93 |
| Profit factor | 1.09 |
| Avg win | $811.77 |
| Avg loss | $1118.98 |
| Expectancy/trade | $39.47 |
| Total realized P&L | $+394.69 |
| Net return | 0.39% |

## Blockers to Graduation
- Need **5 more closed trades** (have 10, need 15)
- Need **+2.61% more return** (have 0.39%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| AUD_USD | 1 | 0 | $+1519.75 | +42.4 | ✅ Active |
| NZD_JPY | 1 | 0 | $+630.67 | +29.0 | ✅ Active |
| CAD_JPY | 1 | 0 | $+310.83 | +21.4 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| EUR_USD | 0 | 1 | $-1695.00 | -33.9 | ✅ Active |
| USD_CAD | 0 | 1 | $-2230.59 | -78.9 | ✅ Active |

## What the Agent Learned (4 adjustments)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.47 in wins vs 0.85 in losses — not predictive (delta=-0.38)
- ⬇️ **session** weight: 0.5 → 0.5 — avg score 0.62 in wins vs 0.81 in losses — not predictive (delta=-0.19)
- ⬇️ **volatility** weight: 0.5 → 0.5 — avg score 0.39 in wins vs 0.66 in losses — not predictive (delta=-0.27)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.59 in wins vs 0.85 in losses — not predictive (delta=-0.26)

## Direction Bias
- Long win rate:  0.56
- Short win rate: 1.0
- Sample: 10 trades

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
- Trades needed: **5** more  (~4 weeks at 1-2/week)
- Return needed: **+2.61%** more  (~$2,605 more profit needed)
- Manual approval required before any real money is deployed.