# Forex Agent — Changelog

---

## v1.5.0 — 2026-10-27 (Recursive Self-Learning + Profit Factor Fixes)

**Baseline reset:** No — all changes tighten or add capability.

### Root cause analysis from 45 closed trades
- Avg win $437 vs avg loss $1,193 (0.37× ratio — need 0.95× for PF≥1.3 at 57.8% WR)
- Only 1 TP hit in 45 trades — TP1 at 2R was never being reached
- 71% stop loss rate — most trades hit stop before 0.5R partial-close trigger
- EUR/JPY: infinite halving bug — float precision caused `already_trailed` to read False after SL set to breakeven, re-triggering Stage 1 every hour (120k→60k→30k→15k→...)
- SHORTs: −$11,371. LONGs: +$66. Directional filter too weak.

### Fixes (all tightening — MINOR)

**1. Infinite-halving bug fixed (forex_trade.py)**
- `already_trailed` now uses unit-count comparison (`current_units < initial_units`) as
  primary check, with float-tolerance SL comparison as fallback. A position that has
  already been partially closed never re-enters Stage 1 regardless of SL rounding.

**2. MFE/MAE tracking added (forex_journal.py)**
- Every hourly journal sync now records `mfe_pl`, `mae_pl`, `mfe_r`, `mae_r` on open
  positions (max favorable / max adverse excursion in $ and R-multiple terms).
- These fields persist to closed trades, giving the optimizer real movement data.

**3. Recursive stop/TP optimizer (forex_optimizer.py)**
- `optimize_stops_and_targets()`: runs on every daily optimizer call (even under the
  weight lock) and analyzes MFE/MAE data to:
  - Track partial-close hit rate per pair (did price reach 0.5R before stop?)
  - Auto-deprioritize pairs with <20% partial-close hit rate (consistently bad entries)
  - Adjust per-pair `stop_atr_mult` in `pair_stop_multipliers`: if avg MAE is shallow
    (<0.3R), stop is too tight; if avg MAE is deep (>0.8R), stop too wide
- forex_research.py reads `pair_stop_multipliers` and uses pair-specific stop distance
  rather than the global 2.0×ATR for all pairs.

**4. Judge gate raised 7.0 → 7.5 (forex_judge.py)**
- Trades scoring 7.0–7.4 had below-average outcomes. Raising the gate reduces trade
  frequency but improves quality. Fewer bad entries is the fastest path to a better
  profit factor.

### What this enables
The recursive loop is now:
  Trade → MFE/MAE recorded hourly → optimizer fits per-pair stop distance → research
  uses learned distance → better entries → fewer full-stop losses → higher profit factor

---

## v1.4.0 — 2026-10-20 (Signal Quality Overhaul — Stop Losses to Profits)

**Post-mortem on 44-trade sample: profit factor 0.52 despite 59% win rate. Avg loss ($1,364) is 3.1× avg win ($437). Five catastrophic losses (>$2K each) account for $12,181 of total losses. Root causes identified and fixed.**

### Root Cause Analysis

- **Optimizer corrupted judge weights** — ran on only 6 trades from the v1.3.0 window, pushing `technical: 7.591` while every other factor dropped to `0.482`. The judge has been effectively broken since Oct 12: a pair could score perfectly on carry, momentum, and R:R but still be vetoed by a flawed technical score, and vice versa.
- **Position sizing catastrophically too large** — avg units on *losses* = 380K vs avg units on *wins* = 137K. The 2% risk rule combined with the 500K unit cap was producing $2,000–$3,200 losses on pairs like EUR/GBP (500K units × 64 pips = $3,217). Winners were averaging far smaller positions.
- **No daily (D1) trend filter** — screener used only H4 candles. Every October catastrophic loss was a H4 signal opposing the D1 structure. USD strengthened throughout Oct; agent was shorting USD pairs into a USD rally (EUR/USD, USD/CAD reversed).
- **No cap on optimizer weight drift** — no guardrail preventing the optimizer from overriding the strategy's core weights. Learning on 6 trades at 100% blend produced worse weights than the priors.

### Fixed

- **G15: D1 trend alignment gate** — `forex_screener.py` now fetches D1 candles alongside H4. Computes D1 MA20/MA50/price and sets `d1_trend = BULLISH | BEARISH | NEUTRAL`. `forex_judge.py` G15 disqualifies any trade where direction opposes D1 trend (LONG blocked when D1 BEARISH, SHORT blocked when D1 BULLISH). Every Oct catastrophic loss fails this gate retroactively.
- **Position size halved** — `MAX_RISK_PCT` 2% → 1%, `MAX_JPY_RISK_PCT` 1.5% → 0.75%, unit cap 500K → 200K. Additional `MAX_LOSS_DOLLARS = $600` hard cap: no trade can risk more than $600 regardless of the formula. This eliminates $2K–$3K catastrophic losses by construction.
- **Min H4 confluence raised** — screener now requires 4/6 signals (up from 3). Stronger signal required before even running the judge.
- **Optimizer weight learning locked until 50 closed trades** — `forex_config.json` now carries `weight_learning_disabled_until_trades: 50`. The optimizer skips weight updates until then, only updating pair stats and direction bias (which are safe on small samples). When learning does resume at 50+ trades, blend is capped at 60% (never 100% data-driven) and weights are bounded to ±40% of base values.
- **Judge weights reset** — `forex_config.json` weights reset to v1.3.0 balanced values: carry=2.5, momentum=2.0, technical=1.5, session=1.0, volatility=1.5, risk_reward=1.5.
- **Deprioritized pairs expanded** — USD/JPY, USD/CAD, EUR/GBP added to deprioritized list alongside USD/CHF (all net negative P&L). Require 7.5+ score and trade at 0.5× size until first win on each.
- **Daily loss limit tightened** — 3% → 2% of equity.

### Expected impact
- D1 filter alone would have blocked all 5 catastrophic losses ($12,181 total).
- Position size halving caps any single loss at ~$600, making even full-stop-out manageable.
- Optimizer lock prevents future weight corruption until statistically meaningful sample size.

### No baseline reset
All changes are guardrails (tightening). Past results stand. Profit factor target remains ≥1.3.

---

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

## v1.3.0 — 2026-10-12 (Exit Mechanics Overhaul — 10× Improvement Plan)

**Post-mortem on 33-trade sample: 22 stop-loss closes produced −$5,137 total; 10 manual closes produced +$2,393. Trades were winning, then being swept by tight trailing stops.**

### Root Cause Analysis
- **Profit factor 0.74 despite 67% win rate** — exits, not entries, are the problem
- **13 of 22 stop-loss closes were wins** — trailing stop swept profitable positions on normal retracements
- **Only 1 TP1 hit in 33 trades** — 2:1 R:R target was too far; market reversed before reaching it
- **JPY losses 3–4× larger than wins** — position sizing at 2% on JPY pairs creates outsized pip exposure (~$94/pip on USD/JPY at $95K NAV)
- **Learned weights overfit on 6 trades** — technical weight inflated to 5.4 from sample too small to be meaningful; best pairs (NZD/JPY, EUR/JPY) are carry+momentum driven

### Fixed
- **TP1: 2.0R → 1.5R** — 40% partial close triggers sooner; captures profit before retracement at 2:1 never arrives
- **Stage 2 trail: 0.75R → 1.5R** behind price — was sweeping positions on normal H4 wicks; now gives 1.5× stop distance of room before the trail fires
- **Stage 2 trigger: 2.0R → 1.5R** — starts trailing at TP1 (consistent with new TP1 level)
- **Time stop: 5 days → 3 days** at 0.5R threshold — stagnant trades (momentum dead) freed at 72h not 120h
- **JPY size cap: 2% → 1.5%** equity (`MAX_JPY_RISK_PCT = 0.015`) — higher pip value per unit on JPY pairs demands smaller percentage to prevent catastrophic pip exposure
- **Judge weights reset and rebalanced**: carry 2.0→2.5, momentum 2.5→2.0, technical 2.0→1.5, risk_reward 1.0→1.5. Overfit learned weights (technical 5.4 from 6 trades) cleared; base weights now reflect observed pair performance over 33 trades.

### No baseline reset
All changes are exit-mechanic tightening and weight rebalancing. Signal criteria unchanged. Past results stand.

---

## v1.2.0 — 2026-09-02 (Risk Controls: Correlation Cap + Cooldown + Earlier Breakeven)

**Post-mortem response to Sep 2 drawdown: −$6,553 across 4 correlated stop-outs.**

### Root Cause Analysis
- **JPY correlation**: USD/JPY ×2, AUD/JPY, CAD/JPY all held simultaneously = one massive BOJ/USD risk factor. When USD/JPY sold off ~80 pips, all four hit stops simultaneously.
- **No early protection**: Breakeven trigger was at 1:1 R:R (77+ pips profit required on USD/JPY). Positions peaked at +10–16 pips and reversed — SL sat at original level with zero protection.
- **Duplicate USD/JPY**: 939K combined units before G2b guard was live, doubling the loss on that pair.

### Fixed
- **G13: JPY correlation cap** — max 2 JPY pair positions open at once (`MAX_JPY_POSITIONS = 2`). USD/JPY, AUD/JPY, CAD/JPY, EUR/JPY, GBP/JPY all count against the same bucket. Would have capped Sep 2 exposure to 2 positions instead of 4.
- **G14: Stop-loss cooldown** — 24h re-entry block after any stop-loss; auto-escalates to 48h if 2+ JPY pairs stopped the same day (macro regime shift signal). USD/JPY is blocked today after this morning's double stop-out.
- **Earlier breakeven trigger** — `TRAILING_TRIGGER_RR` lowered from 1.0 → 0.5 (half the stop distance, not equal). On a 77-pip USD/JPY stop: breakeven now triggers at +38 pips instead of +77. The Sep 2 positions peaked at +16 pips — still wouldn't have saved them, but any future position reaching half its stop distance locks in with no-loss floor.

### No baseline reset
All changes are new guardrails (tightening). Past results stand.

---

## v1.1.0 — 2026-08-31 (Accelerated Learning)

**Goal: faster trade accumulation toward the 30-trade graduation target.**

### Added
- **NZD/JPY, CAD/JPY** added to `CROSS_PAIRS` — same JPY carry thesis as winning AUD/JPY and EUR/JPY; 2 additional qualifying candidates per run
- Pip sizes, ATR healthy ranges, and spread limits wired for both new pairs

### Changed
- **USD/CHF re-enabled** with raised gate (7.5 vs 7.0) and half-size (0.5×) until first win. Was hard-blocked despite scoring 7.84; caution tax replaces permanent freeze.
- **G2b same-pair duplicate guard** — `execute_verdicts` now checks `get_open_pairs()` before placing; skips any pair already held. Prevents today's double USD/JPY entry (two positions opened 2h apart on same pair).

### No baseline reset
All changes are additive (new pairs) or tightening (higher USD/CHF gate, duplicate block). Past results remain valid.

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
