# Forex Trading — Graduation Framework
OANDA Practice Account → Real Money Pathway

---

## Phase 1 — Rule Discovery (Jun 16–30) ✅ COMPLETE
**Goal:** Establish what NOT to do before automating anything.
- [x] Researched 4 major FX platforms; selected OANDA
- [x] Ingested 151 Trading Strategies PDF (FX chapters extracted)
- [x] Ingested Quantpedia, PapersWithBacktest, MQL5, TradingView strategies
- [x] Defined carry trade, momentum+carry combo, HP-filtered MA, triangular arb
- [x] Identified key risk parameters for FX (leverage, session hours, spread costs)

---

## Phase 2 — Infrastructure Build + Paper Activation (Jul 1–14) 🔄 ACTIVE
**Goal:** Automated 24/7 paper trading live on OANDA practice.

### Criteria to complete Phase 2:
- [ ] OANDA practice account created
- [ ] `OANDA_API_KEY` and `OANDA_ACCOUNT_ID` configured in MCP env
- [ ] forex_screener.py scanning 10 major pairs
- [ ] forex_judge.py scoring ≥ 7.0 / 10 before any trade fires
- [ ] forex_trade.py placing orders with stop-loss and take-profit on every trade
- [ ] forex_journal.py logging every open/close with pip P&L
- [ ] GitHub Actions cron running every 4 hours (24/7 due to FX market hours)
- [ ] First 5 paper trades placed and logged

### Guardrails active from day 1:
- Max 5 open pairs at any time
- Max position: 2% account equity per pair
- Stop-loss on EVERY trade: 1.5× ATR(14) from entry
- Take-profit T1: 2× risk (close 50%), T2: 3× risk (close remaining)
- No trading 30 min before/after major data releases (NFP, FOMC, CPI)
- Session filter: prefer London (8AM–4PM GMT) + NY overlap (1PM–5PM GMT)
- Weekend filter: no new positions after 4PM ET Friday; close uncapped positions
- Max daily loss: 3% of account → pause all trading until next session

---

## Phase 3 — Evidence Accumulation (~Jul 15 – Aug 31) 🔒 LOCKED
**Goal:** 30+ closed trades with measurable edge.

### Graduation criteria (ALL must be met):
| Criterion | Target | Gate |
|---|---|---|
| Closed trades | ≥ 30 | Hard minimum |
| Win rate | ≥ 55% | Hard minimum |
| Profit factor | ≥ 1.3 | P / L ≥ 1.3 |
| Realized P&L | Positive | Any positive |
| Max drawdown | ≤ 8% peak-to-trough | Must not exceed |
| Avg R:R on winners | ≥ 1.5:1 | Check vs. stop distance |
| No consecutive losses | ≤ 5 in a row | If hit, pause + review |
| Ruleset stability | Zero changes during Phase 3 | No tweaks while counting |

### Measurement period:
- Start: First day of Phase 3 (target ~Jul 15)
- End: When all 5 criteria met (est. 6–8 weeks at 1–2 closes/week; FX can close faster)
- **No real money until all criteria met simultaneously**

### What to track in journal:
- Entry price, stop-loss pips, take-profit pips
- Exit reason: TP1 hit / TP2 hit / stop-loss / manual
- Carry earned/paid while position was open
- Session (London / NY / Asian) of entry
- Strategy tag (carry, momentum, combo, breakout)

---

## Phase 4 — Soft Launch: Real Money at 10% Scale (~Sep 1–30) 🔒 LOCKED
**Unlock conditions:** All Phase 3 criteria met.

**Scale:** $10,000 USD real account on OANDA (not $100K).

### Rules during Phase 4:
- Run paper and real accounts in PARALLEL (identical signals)
- Real account trades same pairs, same sizing logic, same stops — scaled to $10K equity
- Compare real vs. paper P&L weekly
- Real must track paper within ±7% variance over 4-week period
- No manual overrides on real account

### Scale-up unlock (Phase 4 → Phase 5):
| Criterion | Target |
|---|---|
| Real account P&L | Positive over 4 weeks |
| Real vs. paper tracking | Within ±7% |
| No margin calls | Zero |
| No runaway losses | Max drawdown ≤ 5% of $10K |

---

## Phase 5 — Full Deployment (~Oct 2026) 🔒 LOCKED
**Unlock conditions:** All Phase 4 criteria met.

**Scale progression:**
- Week 1: $50K real
- Week 5: $100K real (if Phase 4 metrics hold)
- Max leverage: 10:1 per position (well below OANDA's 50:1 max)
- Ongoing: monthly graduation reviews, no rushing

---

## Guardrails — Always Active (All Phases)
These NEVER change regardless of phase:

| Guardrail | Rule |
|---|---|
| G1 — Auth | `OANDA_PAPER_TRADE=true` must be set in Phase 2–3 |
| G2 — Position cap | Max 5 open pairs simultaneously |
| G3 — Per-trade risk | Max 2% account equity per trade |
| G4 — Daily loss | Pause if account drops 3% in one day |
| G5 — Stop-loss | Every order must have stop-loss set |
| G6 — No prediction | Never trade earnings/economic releases ± 30min |
| G7 — Score gate | Judge score ≥ 7.0 required; ≥ 8.0 for larger sizing |
| G8 — Spread filter | Skip pair if spread > 3× average spread for that pair |
| G9 — Weekend | No new positions after 4PM ET Friday |

---

## Forex vs. Stock Graduation Comparison

| Dimension | Stocks | Forex |
|---|---|---|
| Market hours | 9:30AM–4PM ET | 24/5 (Sun 5PM–Fri 4PM ET) |
| Phase 3 duration | ~6 weeks (1–2 closes/week) | ~6 weeks (faster cycling possible) |
| Real money start | $10K soft launch | $10K soft launch |
| Target win rate | ≥ 55% | ≥ 55% |
| Primary risk | Stock-specific news | Leverage + spread + carry cost |
| Key advantage | Fundamental research | 24/7 + carry income |

---

## Current Status: 2026-07-06
- Phase 1: ✅ Complete
- Phase 2: 🔄 Active — building infrastructure
- Phase 3: 🔒 Locked (awaiting 30 closed trades)
- Phase 4: 🔒 Locked
- Phase 5: 🔒 Locked
