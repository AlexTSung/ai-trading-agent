# Forex Graduation Report — 2026-10-14

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 7 | ❌ |
| 🎯 Win rate | 55% | 57% | ✅ |
| 💰 Profit factor | 1.50 | 1.41 | ❌ |
| 📈 Expectancy/trade | >$0 | $164.03 | ✅ |
| 🏦 Net return | 3.0% | 1.15% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 7 |
| Wins / Losses | 4W / 3L |
| Win rate | 57% |
| Gross wins | $3,929.12 |
| Gross losses | $2,780.93 |
| Profit factor | 1.41 |
| Avg win | $982.28 |
| Avg loss | $926.98 |
| Expectancy/trade | $164.03 |
| Total realized P&L | $+1,148.19 |
| Net return | 1.15% |

## Blockers to Graduation
- Need **8 more closed trades** (have 7, need 15)
- Need **+1.85% more return** (have 1.15%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| AUD_USD | 1 | 0 | $+1519.75 | +42.4 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| USD_CAD | 0 | 1 | $-2230.59 | -78.9 | ✅ Active |

## What the Agent Learned (5 adjustments)
- ⬇️ **carry** weight: 0.499 → 0.5 — avg score 0.30 in wins vs 0.80 in losses — not predictive (delta=-0.50)
- ⬇️ **momentum** weight: 0.499 → 0.5 — avg score 0.40 in wins vs 1.00 in losses — not predictive (delta=-0.60)
- ⬇️ **session** weight: 0.499 → 0.5 — avg score 0.44 in wins vs 0.83 in losses — not predictive (delta=-0.40)
- ⬇️ **volatility** weight: 0.499 → 0.5 — avg score 0.31 in wins vs 0.70 in losses — not predictive (delta=-0.39)
- ⬇️ **risk_reward** weight: 0.499 → 0.5 — avg score 0.46 in wins vs 0.85 in losses — not predictive (delta=-0.39)

## Direction Bias
- Long win rate:  0.5
- Short win rate: 1.0
- Sample: 7 trades

## Updated Judge Weights
```json
{
  "carry": 0.5,
  "momentum": 0.5,
  "technical": 7.501,
  "session": 0.5,
  "volatility": 0.5,
  "risk_reward": 0.5
}
```

## Estimated Path to Graduation
- Trades needed: **8** more  (~6 weeks at 1-2/week)
- Return needed: **+1.85%** more  (~$1,852 more profit needed)
- Manual approval required before any real money is deployed.