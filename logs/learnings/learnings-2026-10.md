# Learning Agent Report — 2026-10-21

## Performance Summary
| Metric | Value |
|--------|-------|
| Total closed trades | 35 |
| Win rate | 31.4% |
| Wins | 11 |
| Losses | 5 |
| Graduation threshold | ❌ NOT MET (need ≥55%) |

## Detected Patterns (3)

### 1. high_score_poor_outcome (seen 2x)
Judge score ≥8.0 but outcome POOR_TRADE (2x: AAPL, AAPL). Judge may be overweighting news sentiment. Consider raising APPROVE threshold to 8.0.
**Recommended action**: `raise_approve_threshold`

### 2. persistent_judge_overconfidence (seen 9x)
Judge consistently scores -4.4 pts above reviewer (9 trades). Judge is overconfident. Recommend reducing news component weight by 10%.
**Recommended action**: `reduce_news_weight`

### 3. win_rate_summary (seen 35x)
Overall win rate: 31% (11W / 5L / 19N of 35 trades). Below 55% graduation threshold ❌ — not ready for real money.
**Recommended action**: `graduation_check`

## Judge Weight Hints (auto-tuned)
```json
{
  "approve_threshold": 8.0,
  "news_weight_adj": 0.6,
  "last_updated": "2026-10-21",
  "total_trades": 35,
  "scope": "alpaca_equities",
  "round_trips": 30,
  "wins": 4
}
```

## Next Steps
- Review patterns above and confirm which actions to apply
- Run `python3 judge.py session` — it reads `judge_weight_hints.json` for adjustments
- Run `python3 reviewer.py calibration 30` for graduation check