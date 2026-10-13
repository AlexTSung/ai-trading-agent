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

## [v1.4.0] — 2026-10-13

**MINOR** — run_github.sh G1 changed from one-shot-per-day lock to a 50-minute rolling re-run interval, and G2's 4:00 PM cutoff removed, so the morning pipeline actually re-runs research/judge/trade hourly under the new external 24/7 cron-job.org trigger instead of locking the whole day to whichever trigger landed first (which on 2026-10-13 was 12:01 AM ET, market closed).

### Why
Alex confirmed on 2026-10-13 that cron-job.org is intentionally configured to
call every equities workflow's `workflow_dispatch` endpoint every hour, around
the clock — not restricted to market hours or weekdays. `run_github.sh`'s
GUARDRAIL G1 was still the one-shot lock from v1.3.1: `if [ -f
"$LOG_DIR/$DATE.json" ]; then skip`. Once any trigger wrote today's research
file, every later trigger that day — including every one that landed during
actual market hours — treated the day as already handled and skipped outright.

This was not a hypothetical. 2026-10-13's research+judge run landed at 12:01
AM ET (`market_open: false`), scored real candidates off midnight-stale data
(APPROVE: NVDA 8.75, MSFT 8.52, NU 8.87), and then every subsequent hourly
trigger during market hours — when those approvals could have reached the
executor — skipped under G1. `trade.py session` itself was and is correct
(confirmed in the job logs: `{"status": "MARKET_CLOSED", "no_trades": true}`,
sourced from Alpaca's real-time clock via `research.get("market_open")`); the
bug was one level up, in which trigger "won" the one-shot lock for the day.

### Strategy / CLAUDE.md changes
- None to trading criteria, Judge scoring, position sizing, or any Hard Rule.
  This changes pipeline run *frequency*, not what the Judge or executor decide
  once invoked.
- CLAUDE.md's Daily Schedule section rewritten to describe the actual external
  trigger configuration (hourly, all days, no market-hours restriction) in
  place of the stale per-workflow minute list written before the real
  cron-job.org config was confirmed.

### Code changes
- `run_github.sh` GUARDRAIL G1: replaced the daily-file-exists check with a
  50-minute rolling interval (re-run if `$DATE.json` is missing or its mtime
  is more than 50 minutes old).
- `run_github.sh` GUARDRAIL G2 (the 4:00 PM ET cutoff): removed. It only ever
  protected a single daily shot from landing too late to matter; with G1 now
  re-running hourly all day, there is no longer a single "last chance" to
  protect, and an after-hours/overnight run is exactly the continuous
  news/intelligence scanning Alex asked for ("always scanning the internet for
  new data related to any stocks currently in portfolio... or any future
  trades"). `trade.py`'s own `market_open` check is unchanged and remains the
  actual safety control against off-hours order placement — verified against
  2026-10-13's job logs before this change was made, not assumed.
- `morning-pipeline.yml`: updated the `test_mode` input description (G2 is
  gone, so the input no longer has any effect on `run_github.sh`) and the
  schedule-crons comment to stop describing the now-removed "last chance
  before 4:00 PM" framing.

### Why this didn't need a dedup/cooldown change
Running the full pipeline hourly instead of once could in principle rebuy the
same symbol repeatedly. It can't: `trade.py`'s buy path already requires `sym
not in open_syms` (`trade.py:1222`) for both signal-based and dip-buy entries,
so once a buy fills, every later run that same day sees the symbol already
held and will not re-enter it. Avg-down adds are gated separately at a
stricter 8.0 Judge score and are a deliberate add-to-a-position action, not a
duplicate. No new dedup logic was needed or added.

### Comparability
No. Judge criteria, position sizing, stop logic, and every Hard Rule are
unchanged — this only changes how often the existing pipeline is allowed to
run, and running it more often strictly recovers trading opportunities the
one-shot lock was discarding, never adds a new one the strategy wouldn't
otherwise have taken.


## [v1.3.1] — 2026-10-12

**PATCH** — Document the external-trigger fix for GitHub Actions schedule-event
delays: `schedule:` triggers hit a hard ceiling on 2026-10-05 (entire day's
cron block delayed past market close again, despite the Sept 18 retiming
fix). `workflow_dispatch` isn't subject to the same documented high-load
delay, so an external cron service now calls it directly at the same
documented times; in-repo `schedule:` crons stay as a free fallback. No code
change — all four workflows already accepted `workflow_dispatch`. Restores
the intended "fires consistently" behavior; doesn't touch any Judge/strategy
logic.

### Strategy / CLAUDE.md changes
- Daily Schedule section: new "External trigger" subsection explains why
  `schedule:`-only triggering hit a hard ceiling (2026-10-05 measurement)
  and why `workflow_dispatch` from an external cron service isn't subject
  to the same documented delay mechanism.
- "Where automation actually runs" line updated to name the external cron
  service as the primary trigger, `schedule:` as fallback.

### Code changes
- None. Every workflow already accepts `workflow_dispatch` — this is a
  trigger-source change (who calls the existing entry point), not a new
  capability in the YAML or Python layer. No new secrets, no new workflow
  inputs, no change to G1/G2 guardrail logic, which already made repeated
  `workflow_dispatch` calls safe no-ops before this change.

### Comparability
- Does this invalidate the prior performance record? **No.** This changes
  how reliably a pipeline *fires*, not what it decides once it runs. No
  Judge criterion, gate, or sizing rule changed.


## [v1.3.0] — 2026-09-18

**MINOR** — Four independent fixes from the same review pass: restore the
Yahoo fundamentals source and give it real fallbacks, close the two
documented-but-unbuilt options gaps (earnings blackout, VIX vol-regime gate),
validate news-firehose ticker extraction against live data, and retime every
equities cron off GitHub's documented high-load minutes. No Judge criterion,
graduation gate, or sizing rule changed; two previously-undocumented risk
gaps on the options book are now closed, which is exactly the "tighten only"
direction the versioning table treats as MINOR.

### Strategy / CLAUDE.md changes
- Options-improvements list items 3 and 5 marked done (earnings blackout,
  VIX vol-regime gate) with the actual thresholds and call sites.
- Daily Schedule table and its explanatory paragraph updated with the new
  cron minutes and the GitHub high-load-minute finding.

### Code changes
- `research.py get_yahoo_fundamentals()`: root cause of the 2026-09-18
  finding — Yahoo's quoteSummary endpoint now 401s every unauthenticated GET
  (confirmed on 63 of 68 symbols researched that day). Added a session
  cookie + crumb handshake (`_yahoo_handshake()`, cached per process so a
  60+-symbol research run does it once, not per symbol). Judge's quality
  score was silently near-zero for virtually every candidate as a result,
  and every discovery-sourced candidate was structurally unable to clear the
  0.5 quality floor (`judge.score_quality` gives watchlist stocks "benefit of
  the doubt" on missing market cap; discovery stocks get 0, then another
  -0.25 for the always-zero analyst count).
- `research.py get_secedgar_market_cap()` (new): market-cap fallback from
  SEC EDGAR's filed shares-outstanding × a live price, for when Yahoo itself
  is down. Ticker→CIK map cached to disk weekly, same pattern as
  `screener.get_cached_universe()`. Restores only market cap (1.5 of 2.5
  quality points) — EDGAR's XBRL facts don't carry analyst ratings or
  profitability, so those stay unavailable on a real Yahoo outage.
- `research.py get_stooq_price()` (new): last-resort current-price fallback,
  free CSV quote, no key. Price only, not a fundamentals source in its own
  right — used only to fill `current_price` (and the `upside_pct` calc that
  depends on it) when neither Yahoo nor the caller's own price is available.
- `research.py full_research()`: passes `compute_technicals()`'s `last_close`
  into the fundamentals call so the SEC EDGAR fallback has a price to
  multiply against.
- `intelligence.py write_reddit_report()` (new): `scan_reddit()` already ran
  daily inside `morning_intelligence()` and fed the discovery channel, but
  only ever reached `logs/paper/*-intelligence.json` — no standalone report
  the way StockTwits gets one. Now writes `logs/paper/YYYY-MM-DD-reddit.md`.
- `options_screener.py check_earnings_blackout()` (new): options equivalent
  of Hard Rule 9 / `judge.check_earnings_veto` — reuses judge.py's keyword
  classifier (same false-positive guards) and gates new entries in both
  directions (buying or selling), since a long straddle into a known
  catalyst carries its own IV-crush risk. Reads today's equities-side
  intelligence.json when present, plus a direct per-symbol news check always
  — options can run before the morning equities pipeline writes that file.
- `options_screener.py get_vix_regime()` (new): options equivalent of the
  equities Citadel VIX ladder. Calls `intelligence.get_fear_greed()` directly
  (a free FRED lookup) rather than reading equities' file, for the same
  independent-schedule reason. VIX > 25 caps new short-premium at 1 contract
  (contracts are discrete — no fractional half-size exists to fall back to);
  VIX > 35 blocks new short-premium entirely. Only gates short-premium
  structures, using the same `is_short_premium()` distinction the aggregate
  concentration cap already draws. Thresholds match `judge.VIX_HALF_SIZE` /
  `judge.VIX_NO_NEW_POSITIONS` exactly.
- `run_options.py run_scan()`: wired both gates in — earnings check before
  strategy selection (cheapest gate first), VIX regime check before contract
  resolution/execution for short-premium candidates only.
- `screener.py fetch_news_firehose()`: candidate tickers extracted from
  headline text are now validated against a live Alpaca snapshot
  (`batch_snapshots()`, one batched call) before being reported as
  "hot tickers." The old approach matched any 2-5 letter all-caps word
  against a 35-entry hardcoded ignore-list, which real estate acronyms
  (UAE, IRA, ARR, BRICS, CNBC, ...) sail through easily — these were
  reaching research.py and failing downstream with
  `object of type 'NoneType' has no len()`.
- `.github/workflows/morning-pipeline.yml`, `midday-pipeline.yml`,
  `eod-pipeline.yml`: every cron moved off `:00/:15/:30/:45`. Measured fire
  times for Sept 14-18 showed real research landing 4-5 hours late *every
  day*, never once on the nominal target. GitHub's own scheduled-workflow
  docs: the schedule event "can be delayed during periods of high load," and
  "high load times include the start of every hour" — every cron in these
  three files was sitting exactly on that grid. `options-pipeline.yml` was
  already off-grid by accident and left unchanged. `forex-agent.yml` has the
  same issue (every cron on `:00`) but is outside this file's ownership —
  flagged, not changed.
- `tests/test_guardrails.py`: 27 new tests across all four fixes (fundamentals
  fallback chain, Reddit report, options earnings/VIX gates including
  `run_scan()` wiring, news-ticker validation); 234 tests total, all pass.

### Comparability
- Does this invalidate the prior performance record? **No.** No Judge
  scoring criterion, graduation threshold, or position-sizing rule changed.
  The Yahoo/fallback/Reddit/ticker-validation fixes restore data that was
  silently missing — they don't change how any restored data is scored. The
  options earnings and VIX gates are new risk controls that can only prevent
  a trade that would otherwise have been taken, never approve one that
  wouldn't have passed before — same tighten-only direction as every other
  MINOR entry in this file. The cron retiming changes when a pipeline runs,
  not what it decides.


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
