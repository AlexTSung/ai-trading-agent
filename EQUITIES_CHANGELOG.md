# Equities + Options Agent — Changelog

All notable changes to the **Alpaca equities and options agent's** strategy
and rules, not just its code.

**Scope note.** This repo hosts two independent agents (see CLAUDE.md's "Two
Agents, Two Judges"). The OANDA forex agent has its own `VERSION` /
`CHANGELOG.md` and its own `v1.0.0` tag at repo root, created independently.
Sharing one version number across both would violate the same
no-shared-state rule that keeps their Judges separate, so this file and
`EQUITIES_VERSION` are namespaced, and every tag for this agent is prefixed
`equities-v` (e.g. `equities-v1.0.0`) rather than a bare `vX.Y.Z`.

This file exists because a strategy change makes the performance record before
and after it incomparable. Phase 2 had to be declared void for precisely that
reason: the Judge's weights changed, every score moved by up to 2.0 points, and
the "avg Judge ≥ 7.0" graduation criterion silently stopped meaning anything. A
version number and a note about comparability would have caught it in a day
instead of seven weeks.

## What the numbers mean here

Ordinary semver is a statement about an API. For this agent the contract is the
**strategy**, so the levels map to how a change affects the performance record.

| Level | When | Test |
|---|---|---|
| **MAJOR** `X.0.0` | The performance record before and after is **not comparable**. Baseline reset, phase voided, Judge scale or weights changed, graduation criteria changed, a guardrail removed, a documented Hard Rule loosened, a new book or broker added, real money enabled. | *Would I have to reset the baseline to measure this honestly?* |
| **MINOR** `0.X.0` | New capability, backward-compatible and safe with the existing record. New signal or factor, new watchlist symbols, a gate **tightened**, a documented-but-unimplemented rule now enforced, new tooling or analytics. | *Does this add or tighten, without invalidating past results?* |
| **PATCH** `0.0.X` | Bug and correctness fixes that restore intended behaviour. Wrong log message, broken date window, doc corrected to match code, tests added. | *Was the intent always this, and the code simply wrong?* |

Two rules that follow from the table:

- **Loosening is always MAJOR.** Lowering a documented floor changes what the
  agent will trade, so results are not comparable with results collected under
  the stricter rule. Tightening is MINOR. This asymmetry is the same one the
  learned-hint rails use.
- **A MAJOR bump must state whether the baseline resets.** If it does, say so in
  the entry and in CLAUDE.md's Experiment Timeline. A MAJOR change without that
  note is how Phase 2 became unmeasurable.

<!-- versions below -->

## [v1.0.0] — 2026-08-26

**MAJOR** — MVP baseline. First version where the documented ruleset and the
executing code are known to agree, and the first with a regression suite
covering the guardrails.

Everything before this was unversioned. This entry is the reference point for
every later comparison; it is not a claim that the strategy is proven — Phase 2B
has one closed round trip.

### Strategy / CLAUDE.md rules enforced in code

| Rule | Where |
|---|---|
| Tiered stops (20% / 12% / 10% / 5% inverse) | `trade.tier_for`, `stop_pct_for` |
| Conviction sizing 8% / 5% / 3% by Judge score | `trade.SIZE_BY_SCORE` |
| Risk-off ceiling 2.5% + STRONG_BUY only (Dalio) | `trade.regime_adjusted_position_pct`, `judge.macro_regime` |
| VIX ladder — half size > 25, no new positions > 35 (Citadel) | `judge.score_volatility`, `intelligence.get_fear_greed` |
| 20% cash floor | `trade.CASH_RESERVE_PCT` |
| 48h re-entry cooling-off | `trade.cooldown_remaining_hours` |
| 30-day minimum hold above score 8.0 | `trade.min_hold_days_remaining` |
| Pullback-only entries at MA20 × 0.995, never market orders | `research.compute_technicals`, `trade.buy_with_guardrails` |
| Chase veto — RSI > 60 or > 3% above MA20 | `judge.check_chase_veto` |
| Earnings blackout, 3 trading days | `judge._is_upcoming_earnings` |
| $5 price floor and 10% research-vs-execution price check | `trade.MIN_TRADE_PRICE`, `MAX_PRICE_DEVIATION` |
| Phantom equity blocked from trading, excluded from metrics | `trade.detect_phantom_equity`, `reviewer.QUARANTINED_SYMBOLS` |
| Honest equity for every risk budget | `trade.honest_portfolio_value` |
| Judge weight clamp, total ≤ 10.0 | `judge._clamp`, `MAX_TOTAL_SCORE` |
| Learned hints: scope, graduated sample, tighten-only, clamp + log | `judge.load_learned_hints`, `LEARNING_TIERS` |
| Options: defined risk only, IVR ≥ 60 sell / ≤ 20 buy, 21–60 DTE, 16Δ shorts | `options_strategy.py` |
| Options execution cost charged against real quotes | `options_strategy.execution_cost`, `options_trade.fetch_leg_quotes` |
| Options no-arbitrage bounds and short-vol concentration caps | `options_strategy.validate_no_arbitrage`, `aggregate_short_vol_check` |

### State at this baseline

- Honest equity **$91,118.79** (LBGJ carried at cost); reported equity $593,205.89
- Phase 2B baseline $91,455.93 → **−0.37%**
- Open: AAPL +10.27%, DDOG +5.10%. Closed in Phase 2B: NVDA +9.06% (+$436.75)
- LBGJ quarantined, `TRADING_BLOCKED`, ~$502K of phantom equity excluded
- Watchlist 25 symbols across tech, power, healthcare, energy, staples
- **184 guardrail tests**, every new gate mutation-verified
- Real money (Robinhood): **locked**

### Comparability

- This is the reference point. Nothing before it is comparable — pre-2026-08-20
  results were produced by code that contradicted CLAUDE.md.
- Phase 2B remains open. One closed round trip is not a sample; the occurrence
  count, not the return, is the binding graduation constraint.
