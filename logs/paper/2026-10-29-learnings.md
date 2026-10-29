# Trade Learnings — 2026-10-29

Round trips: 34  ·  vetoes analysed: 658  ·  judge days: 52

## Overall
  all closed trades          n=34   win  23.5%  exp  -5.71%  pf  0.24  $    -8,280  [actionable]

## Discovery vs watchlist
  discovery                  n=8    win   0.0%  exp -14.66%  pf  0.00  $    -5,149  [suggestive]
  watchlist                  n=26   win  30.8%  exp  -2.95%  pf  0.44  $    -3,131  [actionable]

## Stop effectiveness by tier
  TIER1  stopped-out share: 0%
    ├─ stopped out           —
    └─ ran to a real exit    n=10   win  30.0%  exp  -0.74%  pf  0.80  $      -176  [actionable]
  TIER2  stopped-out share: 0%
    ├─ stopped out           —
    └─ ran to a real exit    n=5    win   0.0%  exp  -6.92%  pf  0.00  $    -1,241  [suggestive]
  TIER3  stopped-out share: 47%
    ├─ stopped out           n=9    win   0.0%  exp -16.62%  pf  0.00  $    -7,070  [suggestive]
    └─ ran to a real exit    n=10   win  50.0%  exp  -0.24%  pf  0.93  $      +207  [actionable]

## Hold time vs outcome
  0-1d                       n=1    win   0.0%  exp -15.98%  pf  0.00  $      -763  [anecdote]
  16-30d                     n=10   win  30.0%  exp  -6.24%  pf  0.16  $    -2,601  [actionable]
  2-5d                       n=8    win  12.5%  exp  -5.21%  pf  0.05  $    -1,824  [suggestive]
  30d+                       n=6    win  50.0%  exp  +3.83%  pf  2.18  $    +1,517  [suggestive]
  6-15d                      n=9    win  11.1%  exp -10.77%  pf  0.04  $    -4,609  [suggestive]
  avg hold — winners: 33.9d   losers: 15.2d

## Does the Judge score predict the outcome?
  correlation -0.34 on n=25
  7.0-7.9                    n=3    win  33.3%  exp  +0.87%  pf  1.16  $      +543  [anecdote]
  8.0-8.9                    n=12   win  25.0%  exp  -6.41%  pf  0.14  $    -3,282  [actionable]
  9.0+                       n=6    win  16.7%  exp -10.35%  pf  0.05  $    -2,972  [suggestive]
  <7.0                       n=4    win  25.0%  exp  -0.75%  pf  0.75  $       +53  [anecdote]

## Predictive power per criterion
  quality                    r=+0.824  n=5    [suggestive]
  portfolio                  r=-0.398  n=25   [actionable]
  technical                  r=-0.383  n=25   [actionable]
  macro                      r=-0.375  n=25   [actionable]
  news                       r=-0.065  n=25   [actionable]

## Entry conditions
  by RSI band:
    45 and under             n=1    win 100.0%  exp  +4.02%  pf     ∞  $      +187  [anecdote]
    45-60                    n=3    win 100.0%  exp  +3.91%  pf     ∞  $      +519  [anecdote]
    overbought 60+           n=1    win   0.0%  exp  -8.95%  pf  0.00  $      -428  [anecdote]
  by ATR band:
    high 4%+                 n=1    win   0.0%  exp  -8.95%  pf  0.00  $      -428  [anecdote]
    mid 2-4%                 n=4    win 100.0%  exp  +3.94%  pf     ∞  $      +705  [anecdote]
  by entry quality:
    FAIR                     n=4    win  75.0%  exp  +0.70%  pf  1.31  $       +90  [anecdote]
    GOOD                     n=1    win 100.0%  exp  +4.02%  pf     ∞  $      +187  [anecdote]
  by signal:
    STRONG_BUY               n=23   win  21.7%  exp  -6.06%  pf  0.22  $    -5,657  [actionable]
    WEAK_BUY                 n=2    win  50.0%  exp  +0.00%  pf  1.00  $        -1  [anecdote]

## By macro regime
  RISK_ON                    n=26   win  23.1%  exp  -5.70%  pf  0.23  $    -6,089  [actionable]
  UNKNOWN                    n=8    win  25.0%  exp  -5.73%  pf  0.27  $    -2,191  [suggestive]

## What the vetoed trades did next
  (positive forward return = the veto cost money; negative = it earned its keep)
  chase                5d -0.6% (43% up, n=264)   10d -1.0% (39% up, n=273)   20d -2.5% (37% up, n=273)
  discovery_quality    5d -1.7% (22% up, n=9)   10d -1.9% (27% up, n=11)   20d +1.5% (31% up, n=13)
  earnings             5d -0.6% (54% up, n=39)   10d -0.9% (47% up, n=40)   20d +1.4% (53% up, n=34)
  holiday              5d -2.4% (0% up, n=2)   10d -0.1% (33% up, n=3)   20d +8.6% (50% up, n=2)
  micro_cap            5d +1.6% (100% up, n=1)   10d +1.6% (100% up, n=1)
  min_hold             5d +0.1% (40% up, n=5)   10d +3.2% (80% up, n=5)   20d +6.2% (100% up, n=5)
  other                5d +1.3% (46% up, n=80)   10d +1.6% (49% up, n=87)   20d +2.0% (42% up, n=80)
  tier1_protection     5d +2.0% (71% up, n=14)   10d +0.7% (64% up, n=14)   20d +3.7% (67% up, n=12)

---
Confidence: anecdote <5, suggestive <10, actionable 10+. Nothing here changes a parameter; judge.py's LEARNING_TIERS decides how much may affect scoring.

[analytics] machine-readable copy: /home/runner/work/ai-trading-agent/ai-trading-agent/logs/learnings/analytics.json
