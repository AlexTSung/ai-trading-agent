# Forex Agent — Changelog

Versioning follows [Semantic Versioning](https://semver.org/):
- **MAJOR** (`X.0.0`): Breaking strategy changes — new guardrails, regime overhaul, graduation criteria reset
- **MINOR** (`0.X.0`): New features that don't break the existing strategy — new pairs, new signals, new scoring factors
- **PATCH** (`0.0.X`): Bug fixes, threshold tweaks, email/infra changes that don't affect trade logic

To revert to any version:
```bash
git checkout v1.0.0          # inspect files at that version
git checkout master          # return to latest
```

---

## v1.0.0 — 2026-08-26 (MVP / Phase 3 Baseline)

**First production-ready version of the autonomous forex agent.**

### Strategy
- **Pairs**: EUR/USD, GBP/USD, USD/JPY, USD/CHF, AUD/USD, USD/CAD, NZD/USD, EUR/GBP, EUR/JPY, GBP/JPY, AUD/JPY
- **Deprioritized**: USD/CHF (0W/2L, −$550 — excluded from new entries)
- **Judge gate**: score ≥ 7.0 → APPROVE; < 7.0 → SKIP/DISQUALIFIED
- **6-factor scoring** (weights sum to 10): carry, momentum, technical, session, volatility, risk_reward
- **Guardrails G1–G12**: position limits, daily loss guard, 48h cooldown, spread check, phantom equity block, earnings blackout
- **Stops**: tiered by pair (Tier 1 blue-chip: 20%, Tier 2 growth: 12%, Tier 3 speculative: 10%)
- **Exits**: TP1 at 2:1 R:R (close 40%), trail to breakeven; TP2 at 4:1 R:R (close 40%); final 20% trailing

### ATR Regime (tightened in this version)
| Regime | Threshold | Score | Trades? |
|---|---|---|---|
| healthy | ≥ floor | 1.0 | Yes |
| borderline | 60–100% of floor | 0.7 | Yes |
| quiet_ok | 35–60% of floor | 0.55 | Yes (Tokyo/overnight) |
| too_quiet | < 35% of floor | 0.3 | Blocked |
| too_volatile | > 150% of ceiling | 0.4 | Blocked |

### Schedule
- 9 runs/day Mon–Fri: Tokyo session (00, 02, 04, 06, 08 UTC) + London/NY overlap (12, 14, 16, 18 UTC) + NY close (22 UTC)
- Daily briefing email 04:05 UTC → alex.sung@youversion.com

### Phase 3 Graduation Criteria (targets for real-money unlock)
- Closed trades ≥ 30
- Win rate ≥ 55%
- Profit factor ≥ 1.3
- Net P&L > $0
- Max drawdown ≤ 8%
- Max consecutive losses ≤ 5
- **Manual Alex approval required** — no auto-graduation

### Portfolio at Baseline
- NAV: $102,131.67
- Closed trades: 7 (4W/3L, 57.1% WR)
- Net P&L: −$509.96 (dominated by 2× USD/CHF losses)
- Open: AUD/JPY LONG @ 113.731

### Infrastructure
- GitHub Actions: `AlexTSung/ai-trading-agent`
- Pipeline: `run_forex.py` → `forex_graduation.py eod` → `forex_optimizer.py daily`
- Email alerts: `forex_notify.py` via `onfire4jc@gmail.com` → `alex.sung@youversion.com`
- Local: `run_forex.sh` via launchd on Mac
