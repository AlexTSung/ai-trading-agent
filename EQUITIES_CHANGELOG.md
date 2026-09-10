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

## [v1.2.0] — 2026-09-10

**MINOR** — Widen the research discovery funnel: raise per-source ticker caps
across every discovery channel and unlock data that was already being fetched
but discarded, plus fetch a modestly bigger pool at the API layer. No Judge
criterion, gate, or sizing rule changed — more candidates reach `research.py`;
the Judge's existing STRONG_BUY/WEAK_BUY eligibility filter still decides what
gets scored, and every Hard Rule and risk gate is untouched.

### Strategy / CLAUDE.md changes
- None. This only changes how many candidates the Researcher looks at before
  the existing Judge filter runs — no rule in CLAUDE.md changed.

### Code changes
- `intelligence.py get_market_discovery()`: Alpaca movers fetch `top=10` →
  `top=20`, matching most-actives' already-20. The pool this feeds
  (`top_gainers`) was being fetched at half the size research.py could use.
- `research.py _load_social_tickers()`: default `max_tickers` 10 → 20
  (StockTwits screener output already carries up to 50 tickers per day; this
  was discarding 80% of it before today).
- `research.py _extract_discovery_tickers()` / `_load_intelligence_tickers()`:
  default `max_tickers` 15 → 30; per-source slices widened to match what's
  already fetched — `top_gainers[:10]` → `[:20]`, `reddit tickers[:5]` →
  `[:15]` (scan_reddit() already ranks and returns up to 20). Round-robin
  interleaving (added in v1.1.0) is unchanged, so a full most-active batch
  still can't crowd out gainers/Reddit picks.
- `research.py _load_screener_tickers()`: default `max_tickers` 15 → 25;
  `news_firehose hot_tickers[:5]` → `[:10]`, `congressional_buys[:3]` → `[:5]`.
- `screener.py full_scan()`: default `top_n` 30 → 50 — more of the
  already-pre-filtered S&P 500 + Nasdaq 100 universe survives into
  `top_candidates`, with no additional API calls.
- `research.py full_research()`: call sites updated to the new defaults; the
  `discovery_sources` field in the research output now reports all four
  discovery channels (`social_tickers`, `intelligence_tickers`,
  `screener_tickers`, `dip_tickers`) instead of silently omitting screener and
  dip tickers from the summary while still feeding them into research.
- `tests/test_guardrails.py`: updated the one test that encoded the old
  default cap (15) as an assertion; all 207 tests pass.

### Comparability
- Does this invalidate the prior performance record? **No.** Every risk gate,
  Judge scoring criterion, position-sizing rule, and Hard Rule is unchanged.
  This widens the pool of symbols that reach `research.py` and, by extension,
  the Judge's existing eligibility filter — it does not change what the Judge
  approves, how much gets sized, or any stop/hold/cooldown rule. Same
  precedent as v1.1.0's funnel widening, which was also MINOR.


## [v1.1.1] — 2026-09-08

**PATCH** — Widen morning/options execution window from 11:30 AM to 4:00 PM ET: three straight trading days (Sept 3, 4, 7) had zero real research despite green workflow runs because GitHub Actions delayed the entire block of a day's redundant cron triggers together, past the old 105-minute window. Matches midday's proven 6-hour window. No scoring/gate/strategy change -- restores the intended behavior of actually running most days, which the narrow window was defeating.

### Strategy / CLAUDE.md changes
- Daily Schedule table: morning and options windows now run 9:45 AM-2:30 PM / 9:40 AM-2:35 PM ET (was 9:45/9:40-11:15/11:10 AM), with two more backup cron attempts each spread across the wider window, mirroring midday's pattern
- Options-improvements item 2 ("Schedule it") updated to note the widened window
- "Guardrails inside the runners" line: morning/options abort time moved from 11:30 AM to 4:00 PM ET

### Code changes
- `run_github.sh` GUARDRAIL G2: cutoff `11 * 60 + 30` → `16 * 60`
- `run_options_github.sh` GUARDRAIL G2: cutoff `11 * 60 + 30` → `16 * 60`
- `.github/workflows/morning-pipeline.yml`: added `0 17` (1:00 PM ET) and `30 18` (2:30 PM ET) backup crons
- `.github/workflows/options-pipeline.yml`: added `5 17` (1:05 PM ET) and `35 18` (2:35 PM ET) backup crons, offset 5 min from morning's to reduce (not eliminate — the push-retry fix already handles a collision) simultaneous-push races

### Comparability
- Does this invalidate the prior performance record? **No.** No Judge criterion, gate, sizing rule, or scoring weight changed. Research run later in the same trading day scores identically to research run earlier — MA20/RSI signals are computed from daily bars, not intraday ticks. The options side does trade some intraday freshness (IV rank, chain snapshot) for reliability; that tradeoff is documented in run_options_github.sh's GUARDRAIL G2 comment but does not change any risk gate or the options graduation baseline.


## [v1.1.0] — unreleased

**MINOR** — Widen the trade funnel without loosening any risk gate: fix a silently-dead discovery channel (NameError swallowed by a broad except, zero tickers reaching the Judge from Alpaca most-active/gainers/Reddit every day), widen options MAX_SCANNED to cover the full watchlist and TOP_N_CANDIDATES above the position cap, and schedule the options pipeline in GitHub Actions for the first time. Also adds redundant same-window cron triggers to morning/midday/EOD so a GitHub Actions scheduler delay (observed 3.5-9h) can no longer zero out a whole trading day.

### Strategy / CLAUDE.md changes
- 

### Code changes
- 

### Comparability
- Does this invalidate the prior performance record? yes / no — why


## [v1.0.1] — unreleased

**PATCH** — Fix naive UTC clock mislabelled as ET across the daily pipeline (journal, reviewer, research, judge, trade, learning_agent, learning_analytics); restores intended behavior, no strategy change

### Strategy / CLAUDE.md changes
- 

### Code changes
- 

### Comparability
- Does this invalidate the prior performance record? yes / no — why


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
