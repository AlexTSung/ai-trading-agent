# Trade Learnings — 2026-10-13

Round trips: 30  ·  vetoes analysed: 540  ·  judge days: 41

## Overall
  all closed trades          n=30   win  13.3%  exp  -7.53%  pf  0.11  $    -9,770  [actionable]

## Discovery vs watchlist
  discovery                  n=8    win   0.0%  exp -14.66%  pf  0.00  $    -5,149  [suggestive]
  watchlist                  n=22   win  18.2%  exp  -4.94%  pf  0.21  $    -4,621  [actionable]

## Stop effectiveness by tier
  TIER1  stopped-out share: 0%
    ├─ stopped out           —
    └─ ran to a real exit    n=9    win  22.2%  exp  -2.88%  pf  0.31  $    -1,068  [suggestive]
  TIER2  stopped-out share: 0%
    ├─ stopped out           —
    └─ ran to a real exit    n=5    win   0.0%  exp  -6.92%  pf  0.00  $    -1,241  [suggestive]
  TIER3  stopped-out share: 56%
    ├─ stopped out           n=9    win   0.0%  exp -16.62%  pf  0.00  $    -7,070  [suggestive]
    └─ ran to a real exit    n=7    win  28.6%  exp  -2.26%  pf  0.52  $      -391  [suggestive]

## Hold time vs outcome
  0-1d                       n=1    win   0.0%  exp -15.98%  pf  0.00  $      -763  [anecdote]
  16-30d                     n=8    win  12.5%  exp  -8.98%  pf  0.03  $    -3,013  [suggestive]
  2-5d                       n=8    win  12.5%  exp  -5.21%  pf  0.05  $    -1,824  [suggestive]
  30d+                       n=5    win  40.0%  exp  +0.90%  pf  1.23  $      +625  [suggestive]
  6-15d                      n=8    win   0.0%  exp -12.61%  pf  0.00  $    -4,795  [suggestive]
  avg hold — winners: 28.5d   losers: 15.2d

## Does the Judge score predict the outcome?
  correlation -0.512 on n=21
  7.0-7.9                    n=2    win   0.0%  exp  -7.95%  pf  0.00  $      -349  [anecdote]
  8.0-8.9                    n=10   win  10.0%  exp  -8.72%  pf  0.03  $    -3,742  [actionable]
  9.0+                       n=5    win   0.0%  exp -13.04%  pf  0.00  $    -3,109  [suggestive]
  <7.0                       n=4    win  25.0%  exp  -0.75%  pf  0.75  $       +53  [anecdote]

## Predictive power per criterion
  portfolio                  r=-0.441  n=21   [actionable]
  technical                  r=-0.259  n=21   [actionable]
  news                       r=-0.181  n=21   [actionable]

## Entry conditions
  by RSI band:
    45-60                    n=1    win 100.0%  exp  +2.34%  pf     ∞  $      +107  [anecdote]
    overbought 60+           n=1    win   0.0%  exp  -8.95%  pf  0.00  $      -428  [anecdote]
  by ATR band:
    high 4%+                 n=1    win   0.0%  exp  -8.95%  pf  0.00  $      -428  [anecdote]
    mid 2-4%                 n=1    win 100.0%  exp  +2.34%  pf     ∞  $      +107  [anecdote]
  by entry quality:
    FAIR                     n=2    win  50.0%  exp  -3.30%  pf  0.26  $      -322  [anecdote]
  by signal:
    STRONG_BUY               n=20   win  10.0%  exp  -8.37%  pf  0.06  $    -6,960  [actionable]
    WEAK_BUY                 n=1    win   0.0%  exp  -4.02%  pf  0.00  $      -187  [anecdote]

## By macro regime
  RISK_ON                    n=22   win   9.1%  exp  -8.19%  pf  0.06  $    -7,579  [actionable]
  UNKNOWN                    n=8    win  25.0%  exp  -5.73%  pf  0.27  $    -2,191  [suggestive]

## What the vetoed trades did next
  (positive forward return = the veto cost money; negative = it earned its keep)
  chase                5d -0.3% (43% up, n=244)   10d -1.0% (40% up, n=248)   20d -1.4% (41% up, n=212)
  discovery_quality    5d -1.9% (25% up, n=8)   10d -1.8% (22% up, n=9)   20d +2.8% (33% up, n=9)
  earnings             5d -1.1% (33% up, n=21)   10d -1.5% (30% up, n=23)   20d +0.1% (42% up, n=19)
  holiday              5d -2.4% (0% up, n=2)   10d -0.1% (33% up, n=3)   20d +8.6% (50% up, n=2)
  min_hold             5d -5.0% (0% up, n=1)   10d -5.0% (0% up, n=1)
  other                5d +1.8% (49% up, n=67)   10d +2.2% (52% up, n=71)   20d +2.5% (38% up, n=63)
  tier1_protection     5d +2.6% (88% up, n=8)   10d +2.5% (100% up, n=8)   20d +4.9% (100% up, n=8)

---
Confidence: anecdote <5, suggestive <10, actionable 10+. Nothing here changes a parameter; judge.py's LEARNING_TIERS decides how much may affect scoring.

[analytics] machine-readable copy: /home/runner/work/ai-trading-agent/ai-trading-agent/logs/learnings/analytics.json
