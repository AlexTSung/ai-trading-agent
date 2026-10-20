# Forex Graduation Report — 2026-10-20

## Verdict: ❌ NOT YET READY

## Graduation Criteria
| Criterion | Required | Current | Status |
|-----------|----------|---------|--------|
| 📊 Closed trades | 15 | 13 | ❌ |
| 🎯 Win rate | 55% | 54% | ❌ |
| 💰 Profit factor | 1.50 | 1.11 | ❌ |
| 📈 Expectancy/trade | >$0 | $49.57 | ✅ |
| 🏦 Net return | 3.0% | 0.64% | ❌ |

## Performance Detail
| | |
|---|---|
| Total closed trades | 13 |
| Wins / Losses | 7W / 6L |
| Win rate | 54% |
| Gross wins | $6,318.23 |
| Gross losses | $5,673.80 |
| Profit factor | 1.11 |
| Avg win | $902.60 |
| Avg loss | $945.63 |
| Expectancy/trade | $49.57 |
| Total realized P&L | $+644.44 |
| Net return | 0.64% |

## Blockers to Graduation
- Need **2 more closed trades** (have 13, need 15)
- Need **+2.36% more return** (have 0.64%, need 3.0%)

## Per-Pair Stats
| Pair | Wins | Losses | Total P&L | Total Pips | Status |
|------|------|--------|-----------|------------|--------|
| USD_JPY | 2 | 0 | $+2398.50 | +113.2 | ✅ Active |
| CAD_JPY | 2 | 0 | $+1758.45 | +83.3 | ✅ Active |
| AUD_USD | 1 | 1 | $+885.23 | +22.0 | ✅ Active |
| NZD_JPY | 1 | 0 | $+630.67 | +29.0 | ✅ Active |
| EUR_JPY | 1 | 0 | $+10.87 | +75.8 | ✅ Active |
| USD_CHF | 0 | 2 | $-550.34 | -15.3 | ⛔ Deprioritized |
| AUD_JPY | 0 | 1 | $-563.35 | -43.8 | ✅ Active |
| EUR_USD | 0 | 1 | $-1695.00 | -33.9 | ✅ Active |
| USD_CAD | 0 | 1 | $-2230.59 | -78.9 | ✅ Active |

## What the Agent Learned (4 adjustments)
- ⬇️ **momentum** weight: 0.5 → 0.5 — avg score 0.51 in wins vs 0.80 in losses — not predictive (delta=-0.29)
- ⬇️ **session** weight: 0.5 → 0.5 — avg score 0.64 in wins vs 0.79 in losses — not predictive (delta=-0.15)
- ⬇️ **volatility** weight: 0.5 → 0.5 — avg score 0.41 in wins vs 0.67 in losses — not predictive (delta=-0.26)
- ⬇️ **risk_reward** weight: 0.5 → 0.5 — avg score 0.63 in wins vs 0.85 in losses — not predictive (delta=-0.22)

## Direction Bias
- Long win rate:  0.6
- Short win rate: 0.33
- Sample: 13 trades

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
- Trades needed: **2** more  (~2 weeks at 1-2/week)
- Return needed: **+2.36%** more  (~$2,356 more profit needed)
- Manual approval required before any real money is deployed.