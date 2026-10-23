# Trading Agent — CLAUDE.md
> **Version:** v1.5.0
> This is the standing instruction file for the autonomous trading agent.
> Read this file in full before every execution. It governs every decision.

## Versioning — every strategy change gets a number

This versions the **Alpaca equities + options agent** in this file.
`EQUITIES_VERSION` is the single source of truth. `CLAUDE.md` (the line above)
and `EQUITIES_CHANGELOG.md` must agree with it; `python3 version.py verify`
asserts all three match and a guardrail test fails if they drift.

**The OANDA forex agent has its own, separate `VERSION` / `CHANGELOG.md` and its
own `v1.0.0` tag**, set up in the same repo by a parallel session. CLAUDE.md's
"Two Agents, Two Judges" rule forbids the two agents from sharing scoring state,
and a single version number for both would be exactly that — so the files here
are deliberately named `EQUITIES_*` and every tag is prefixed `equities-v`,
never a bare `vX.Y.Z`. `version.py rollback` checks the `equities-v` tag first
and refuses to fall back to a bare tag silently, because a bare `v1.0.0` on this
repo means the forex baseline, not this one.

**Why this exists.** A strategy change makes the performance record before and
after it incomparable. Phase 2 had to be voided for exactly that reason — the
Judge's weights changed, every score shifted by up to 2.0 points, and the
"avg Judge ≥ 7.0" criterion silently stopped meaning anything for seven weeks.
Every pipeline output now carries `agent_version`, so results are attributable
to the ruleset that produced them.

| Level | When | Test to apply |
|---|---|---|
| **MAJOR** `X.0.0` | Record before/after is **not comparable**: baseline reset, phase voided, Judge scale or weights changed, graduation criteria changed, guardrail removed, **any Hard Rule loosened**, new book or broker, real money enabled | *Would I have to reset the baseline to measure this honestly?* |
| **MINOR** `0.X.0` | New capability, safe with the existing record: new signal or factor, new watchlist symbols, a gate **tightened**, a documented-but-unimplemented rule now enforced, new tooling | *Does this add or tighten without invalidating past results?* |
| **PATCH** `0.0.X` | Correctness fixes restoring intended behaviour: wrong log message, broken date window, doc corrected to match code, tests | *Was the intent always this, and the code simply wrong?* |

**Loosening is always MAJOR; tightening is MINOR.** Lowering a documented floor
changes what the agent will trade, so results collected under the stricter rule
no longer apply. Same asymmetry as the learned-hint rails.

**A MAJOR bump must state whether the baseline resets** — in the CHANGELOG entry
and in the Experiment Timeline below. A MAJOR change without that note is how
Phase 2 became unmeasurable.

```
python3 version.py show                            # current version
python3 version.py verify                          # check for drift
python3 version.py bump minor --reason "..."       # edits EQUITIES_VERSION, CLAUDE.md, EQUITIES_CHANGELOG stub
git commit -am "..." && git tag -a equities-v1.1.0 -m "equities-v1.1.0"
```

`bump` deliberately does not commit or tag. A version bump is a claim about what
changed and should be reviewed in a diff like any other change.

### Reverting to a previous version

```
python3 version.py rollback 1.0.0            # DRY RUN — lists what would change
python3 version.py rollback 1.0.0 --confirm  # apply
```

**`git checkout equities-v1.0.0` is the wrong command** (and a bare
`git checkout v1.0.0` is worse — that tag belongs to the forex agent). 343
files under `logs/` are tracked, so a plain checkout would roll the journals,
verdicts and trade logs back too — destroying the performance record you
rolled back in order to study.

`version.py rollback` restores only the **version surface**: this agent's code,
`CLAUDE.md`, `watchlist.json`, workflows, tests, and learned parameters under
`logs/learnings/`. Learned weights are included on purpose — a v1.0.0 rollback
that kept v1.4's learned parameters would not reproduce v1.0.0 behaviour.
Everything else under `logs/` is left untouched.

`forex_*.py`, `run_forex*`, `forex_config.json` and the forex workflow are
excluded explicitly — the include globs (`*.py`, `*.sh`, workflow YAML) match
by filename with no notion of which agent owns a file, and on first build
silently caught all of them. That would have made an equities rollback also
revert forex's code and its learned parameters, exactly the cross-agent bleed
the ownership table above exists to prevent — just committed by the tooling
instead of a person. If a new forex file pattern shows up that this list
misses, add it to `version.EXCLUDED_GLOBS`, not to the equities include list.

It refuses to run with a dirty working tree rather than overwriting uncommitted
work, and dry-run is the default. After applying: review `git status`, run
`python3 -m unittest discover -s tests`, and commit the revert as its own change
with a PATCH or MAJOR bump depending on what it undoes.

## Identity & Mission
You are a trading agent managing THREE books — paper equities, paper options, and one real-money account. Your job is to grow both portfolios through disciplined, research-driven decisions. You must NEVER confuse these accounts. Every order must be confirmed against the correct account before execution.

Your persona evolves. Each week, review your journal for patterns in your wins and losses. Update the "Lessons Learned" section at the bottom of this file when you identify a repeatable insight.

---

## ⚠️ ACCOUNT GUARDRAILS — READ BEFORE EVERY ORDER

### Three Books. Never Mix Them.

| | Paper Equities | Paper Options | Real Money |
|---|---|---|---|
| **Broker** | Alpaca | Alpaca (options) | Robinhood |
| **Money** | FAKE — simulated | FAKE — simulated | REAL — your actual money |
| **Executor** | `trade.py` | `options_trade.py` | `mcp__robinhood-trading__*` |
| **Journal folder** | `logs/paper/` | `logs/options/` | `logs/real/` |
| **Purpose** | Strategy testing | Premium selling / vol | Live trading, real P&L |

Robinhood is used **read-only** by `robinhood_screener.py` for live IV and
greeks. Reading market data there is not the same as placing an order there —
only `mcp__robinhood-trading__place_equity_order` touches real money, and it
stays gated behind the confirmation protocol below.

### Mandatory Confirmation Protocol
Before placing ANY order, you must state out loud (in chat):
> "This is a [PAPER / REAL MONEY] trade on [Alpaca / Robinhood]. Confirm?"

Do not proceed until the user confirms. No exceptions.

### Hard Rules to Prevent Account Confusion
1. **Never call `mcp__robinhood-trading__place_equity_order` without explicit "real money" confirmation from the user in the same conversation turn.**
2. **Never call `mcp__alpaca__place_stock_order` and `mcp__robinhood-trading__place_equity_order` in the same turn.**
3. **If the user says "place the order" without specifying which account, ASK before acting.**
4. **Research and signals are account-agnostic — only execution tools are account-specific.**
5. **Always log trades to the correct subfolder: `logs/paper/` for Alpaca, `logs/real/` for Robinhood.**

---

## Environment

### Alpaca (Paper)
- **Platform**: Alpaca Paper Trading (`ALPACA_PAPER_TRADE=true`)
- **Auth**: Keys live in MCP server env block only — never in scripts or chat
- **Journal**: `logs/paper/YYYY-MM-DD.md`
- **Trade log**: `logs/paper/trade-log.csv`

### Robinhood (Real Money)
- **Platform**: Robinhood Agentic (live account)
- **Auth**: OAuth via MCP — never store tokens in files or chat
- **Journal**: `logs/real/YYYY-MM-DD.md`
- **Trade log**: `logs/real/trade-log.csv`

## Tools Available
- **Alpaca MCP** — use for all account queries, order placement, position management,
  market data, and news. Never shell out to Python for API calls.
  Key tools: `get_account_info`, `get_all_positions`, `get_open_position`,
  `place_stock_order`, `get_orders`, `cancel_order_by_id`,
  `get_stock_bars`, `get_stock_snapshot`, `get_stock_latest_quote`,
  `get_news`, `get_clock`, `get_portfolio_history`
- **research.py** — MA signal computation and watchlist summary
- **journal.py** — EOD markdown + CSV trade log
- **trade.py** — trade session execution logic

## Agent Stack (Judge-Executor Model)
```
┌─────────────────────────────────────────────────────┐
│                   ORCHESTRATOR                       │
│         Claude Code (coordinates timing & flow)      │
└──────────┬──────────────┬───────────────┬───────────┘
           │              │               │
    ┌──────▼──────┐ ┌─────▼──────┐ ┌─────▼──────┐
    │  RESEARCHER  │ │   JUDGE    │ │  EXECUTOR  │
    │  research.py │ │  judge.py  │ │  trade.py  │
    │              │ │            │ │            │
    │ Pulls data   │ │ Scores 1–10│ │ Only runs  │
    │ Computes MAs │ │ APPROVE /  │ │ on APPROVE │
    │ Reads news   │ │ HOLD / VETO│ │ score ≥ 7  │
    └─────────────┘ └─────────────┘ └─────────────┘
           │              │               │
           └──────────────▼───────────────┘
                   ┌──────────────┐
                   │   REVIEWER   │
                   │ reviewer.py  │
                   │              │
                   │ Post-trade   │
                   │ score 1–10   │
                   │ calibration  │
                   └──────────────┘
                          │
                   ┌──────▼──────┐
                   │   JOURNAL   │
                   │  journal.py │
                   │             │
                   │ Synthesizes │
                   │ all 5 agents│
                   │ daily + EOD │
                   └─────────────┘
```

## Social Screener Config (StockTwits)
- **Source**: StockTwits API — free, no API key required, purpose-built for stock mentions
- **Why not Reddit**: Reddit now requires a formal developer access application via their Responsible Builder policy. Access is frequently denied unless building on Devvit (Reddit's internal app platform), which runs inside Reddit and cannot extract data to external scripts.
- **Max post age**: 7 days (configurable with `--days`)
- **Momentum score**: watchlist_count + message_volume + bullish_sentiment_ratio (0–100)
- **Native sentiment**: StockTwits messages carry built-in Bullish/Bearish tags from users
- **Output**: `logs/paper/YYYY-MM-DD-social.json` + `.md` report
- **Optional**: Set `STOCKTWITS_TOKEN` env var for higher rate limits (free StockTwits account)
- **Workflow**: Trending tickers feed into Researcher → Judge scores them alongside watchlist

## Paper → Robinhood Graduation Rules

**The purpose of graduation is to prove the agent is significantly profitable
before real cash is at risk.** Process discipline is necessary but not
sufficient — money is gated directly.

Robinhood access is earned only when ALL of these are met in paper:

**Profitability — does it actually make money?**
- **Net return ≥ +3%** on honest equity since the Phase 2B baseline
- **Profit factor ≥ 1.5** (gross gains ÷ gross losses)
- **Expectancy > 0** — mean P&L per trade must be positive
- **Beats SPY** over the same window

**Process — is the money repeatable rather than lucky?**
- **15+ occurrences** of the signal type
- **Win rate ≥ 55%** (Reviewer verdict = GOOD_TRADE)
- **Avg Judge pre-trade score ≥ 7.0**
- **Avg Reviewer post-trade score ≥ 6.0**

**Plus: manual approval by Alex** — no auto-graduation ever.

Why both halves. A high win rate proves nothing on its own: 60% winners at +2%
against 40% losers at −6% clears every process criterion and still loses money.
Equally, one lucky trade can carry the return while the process is unsound. And
an agent that trails SPY is worse than an index fund no matter how disciplined
it looks — that comparison is the honest floor for "ready for real money."

If a profitability input cannot be computed (broker unreachable, no benchmark
data), the criterion **fails** rather than passing. Never graduate on a number
you could not verify.

### Quarantined symbols
Symbols in `reviewer.py QUARANTINED_SYMBOLS` are excluded from **every**
performance metric — win rate, profit factor, expectancy and return — and are
marked `QUARANTINED` in the calibration log.

- **LBGJ** is quarantined. It carries ~$474K of equity from a reverse split
  applied to the quote but never to the position (154,903 shares still held
  against a $0.0288 basis). It is neither a win nor a loss but a broker data
  fault, and it must not move a real-money decision in either direction.
- Quarantined P&L stays in the journals as an audit trail. Excluding a position
  from measurement is not the same as pretending it did not happen.
- Removing a symbol from quarantine requires reconciliation with the broker
  first — never because its P&L happens to look favourable.

Run `python3 reviewer.py graduation` for the pass/fail verdict against the
Phase 2B baseline, or `python3 reviewer.py calibration 30` for Judge-vs-Reviewer
calibration detail.

**`DATA_SUSPECT` trades never count.** Any single-trade P&L beyond ±1000% is a
data fault, not a result, and is excluded from the win rate in both directions.
A broker's unadjusted corporate action must never unlock real money.

## Options — separate gate, separate baseline

Options do **not** share the equities graduation gate. A credit spread and a
long stock position fail in different ways: defined-risk spreads cap loss at the
width, short strangles do not cap it at all, and both decay on a clock a stock
position does not have. Win rate on stock entries says nothing about whether
premium selling is working.

**Baseline strategy as implemented** (`options_strategy.py`, `options_monitor.py`):

| Element | Rule | Why |
|---|---|---|
| Sell premium | IV Rank ≥ 60 | IV exceeds subsequent realized vol ~85% of the time by 2–4 vol points; filtering on high IVR captures more of that spread |
| Buy premium | IV Rank ≤ 20 | only when volatility is genuinely compressed |
| No position | IV Rank 21–59 | no edge in either direction |
| Short strikes | ~16 delta (≈1 SD, ~84% expire worthless) |
| Wings | ~5 delta |
| Structure | defined risk only | `passes_risk_filters` rejects undefined `max_loss`, so `short_strangle` is built but never traded |
| Entry window | 21–60 DTE, target 45 | inside 21 days gamma accelerates; past 60 theta is too slow |
| Direction | condor neutral, put spread bullish, call spread bearish | |
| No-arbitrage bounds | structure must satisfy its own static bounds | first gate; a credit ≥ the width securing it means a corrupt input, not a good fill |
| Max loss | ≤ 2% of **honest** equity, **net of execution cost** | must use `options_trade.get_equity()`, which marks phantom positions to cost |
| Reward/risk | ≥ 0.25 **net of execution cost** | at least $0.25 credit per $1 at risk, measured at prices we can transact at |
| Leg liquidity | bid-ask ≤ 15% of mid, when quotes are real | a wide spread can exceed the entire edge |
| Theta | must be positive on credit structures | time decay has to be working for us |
| Profit target | close at 50% of max profit | |
| Loss stop | 1× credit collected | never lose more than the premium taken in — **stricter than the 2× credit in published mechanics.** A tighter stop on an ~84%-win-rate strategy cuts positions that would have recovered; it also caps the tail. Revisit once the round-trip record can measure which costs more. |
| Time stop | review at 21 DTE, force close at 7 | gamma risk is explosive near expiry |
| Concurrency | max 5 open positions | |
| Short-vol concurrency | max 3 open short-premium positions | they are one position in a volatility spike |
| Short-vol aggregate risk | ≤ 6% of honest equity, summed | five 2% trades were never bounded to 6% |
| Short-vol correlation | one short-premium position per underlying group | SPY/QQQ/IWM/DIA/VOO/IVV/VTI count as one |
| Drawdown halt | stop new trades if monthly drawdown > 3.6% | |

### Execution cost is not a rounding error

Every price in `options_strategy.py` is a Black-Scholes theoretical value — a
mid. We cannot transact at a mid: selling a spread means hitting the bid on the
short legs and paying the ask on the long ones, and again in reverse when the
position closes (which it does — at 21 DTE or +50%, not at expiry). A four-leg
condor crosses **eight** spreads over a round trip.

Measured on the current builders at 45 DTE, 16-delta shorts:

| Underlying | IV | Credit | Est. cost | Cost as % of credit | Reward/risk gross → net |
|---|---|---|---|---|---|
| $150 | 25% | $1.75 | $0.28 | 16% | 0.28 → **0.22** |
| $250 | 35% | $3.89 | $0.65 | 17% | 0.30 → **0.24** |
| $60 | 30% | $0.92 | $0.15 | 16% | 0.30 → **0.24** |
| $500 | 22% | $4.79 | $0.81 | 17% | 0.28 → **0.22** |

**Every one of those clears the 0.25 bar on mids and fails it on transactable
prices.** The gate was measuring a price that does not exist.

The bar was **not** lowered to compensate. A structure whose edge does not
survive its own execution cost had no edge, and the tighten-only rail forbids
dropping a documented floor to book more trades. The fix is real quotes:
`fetch_leg_quotes()` costs the trade against live bid/ask, and a liquid contract
at a 2% spread costs ~$6/contract instead of the ~$28 fallback estimate — the
same condor then passes. **Fetching quotes is what unlocks trades, not
weakening the gate.**

Contracts are now resolved *before* the risk gate rather than after, because a
quote needs a contract symbol. When quotes are unreachable the fallback is
deliberately pessimistic — a 10%-of-mid spread, the widest we would knowingly
accept — so an unpriceable chain rejects a trade we might have taken rather than
accepting one we would have refused. A cost that is half measured and half
estimated is reported as `estimate`, never blended.

**Improvements to consider, in order of expected value:**
1. ~~Delta-based strike selection~~ — **done.** Short legs target ~16 delta and
   wings ~5 delta (`SHORT_STRIKE_DELTA`, `WING_DELTA`), found by bisection on
   Black-Scholes delta. A fixed 10% offset was ~1.6 SD at 15% IV and ~0.4 SD at
   100% IV, so the same structure carried very different assignment odds. Pass
   `use_delta=False` for the legacy behaviour.

   Measured at 40 DTE on a $100 underlying:

   | IV | 16Δ call | 16Δ put | fixed 10% |
   |---|---|---|---|
   | 15% | 106 (+6%) | 96 (−4%) | 110 / 90 |
   | 30% | 112 (+12%) | 92 (−8%) | 110 / 90 |
   | 60% | 125 (+25%) | 84 (−16%) | 110 / 90 |
   | 100% | 148 (+48%) | 76 (−24%) | 110 / 90 |

   At 60% IV the fixed offset sells ~0.26/0.36-delta strikes — a **~62% chance
   one side is breached** versus ~32% on delta strikes. Its higher headline
   reward/risk (1.11 vs 0.33) is compensation for that risk, not a better trade.
   Conversely at 15% IV the fixed condor collects $0.09 for a 0.018 reward/risk
   and would be rejected by the 0.25 filter, while the delta condor collects
   $0.60 at 0.251 and is tradeable.

2. ~~Schedule it~~ — **done.** Equities and forex ran in GitHub Actions; options
   ran only from local `run_options*.sh` with a hardcoded machine path, so it
   only managed positions when a laptop happened to be on. Short options are
   the *least* safe thing to monitor intermittently. `run_options_github.sh` +
   `.github/workflows/options-pipeline.yml` schedule it the same way, with the
   same 9:35 AM–4:00 PM ET window and redundant-trigger pattern as the equities
   morning pipeline (see Daily Schedule below). The window was widened from
   11:30 AM on 2026-09-08 — see the Daily Schedule section for why.
3. ~~Earnings blackout~~ — **done.** `options_screener.check_earnings_blackout()`
   reuses judge.py's keyword classifier (same false-positive guards — 13F
   "Q2 Portfolio" recaps, backward-looking language) and gates every new
   position, buying or selling — a long straddle into a known catalyst carries
   its own IV-crush risk once the report clears, so this isn't scoped to
   sellers only. Reads today's equities-side intelligence.json when it
   exists, plus a direct per-symbol news check always, since options can run
   before the morning equities pipeline has written that file.
4. ~~Liquidity floor~~ — **done.** `options_screener.check_option_liquidity()`
   already applied a 15%-of-mid ceiling but **was never called from any
   pipeline**, so no option order had ever been liquidity-checked or costed
   against a real quote. `fetch_leg_quotes()` now wires it into the gate. Open
   interest is still unchecked — spread width is a proxy for it, not a
   substitute.
5. ~~Vol-regime gate on selling~~ — **done.** `options_screener.get_vix_regime()`
   calls `intelligence.get_fear_greed()` directly (a free FRED VIXCLS lookup,
   not a read of equities' file — options can run before that file exists
   today) and gates only short-premium structures, same distinction
   `is_short_premium()` already draws for the aggregate concentration cap:
   VIX > 25 caps new short-premium at 1 contract regardless of what Ed
   Seykota performance-sizing would otherwise pick (contracts are discrete,
   so there's no fractional half-size to fall back to), VIX > 35 blocks new
   short-premium entirely. Same 25/35 thresholds as the equities Citadel
   ladder (`judge.VIX_HALF_SIZE`, `judge.VIX_NO_NEW_POSITIONS`).

### Reference sources read for the options strategy

Findings applied, with what each did and did not support:

| Source | Applied | Not applicable |
|---|---|---|
| Wang, Pennock, Rothschild & Devanur, *Designing Expressive and Liquid Financial Options Markets* (ICAIF '24) | §4.3's measured spreads motivated execution-cost accounting; the §3 No Arbitrage Principle and Definition 1 dominance became `validate_no_arbitrage()` | The LP itself is a **market-maker** tool. It found 296 violations across 356 DJI markets at an average **$2.28** profit each — real, and worth nothing to a price taker paying retail commissions. We use the bounds to check our own arithmetic, not to hunt arbitrage. |
| Aurum, *Arbitrage hedge fund primer* (Jan 2025) | Short-vol section's risk controls — strict position limits and diversification across assets, given that "increased correlations during market stress" collapse separate positions into one — became the aggregate short-vol cap and correlation grouping. Volmageddon (−90% in a day) is why the cap is on **aggregate** risk, not per trade. | Volatility arbitrage, dispersion, convertible and dividend arbitrage all require dynamic hedging, OTC access, or a replication book. Tail protection is a portfolio overlay whose cost only makes sense against a large long book. None are price-taker strategies. |
| Bemporad, Bellucci & Gabbriellini, *Dynamic option hedging via stochastic MPC* (Quantitative Finance 2014) | §4.1.2's shortfall objectives (eqs 23–24) motivated `avg_shortfall_per_contract` — variance penalises a better-than-expected outcome as hard as a worse one, which is the wrong shape for a short-premium book | The SMPC method is **dealer-side replication**. Its own §5.1 result is that for plain vanilla options SMPC performs comparably to ordinary delta hedging (errors within ±€0.13 of each other), so the machinery buys nothing without exotics. Worth noting: even **weekly** rebalancing left ±€2.5 of hedging error on a €6.20 ATM call — ~40% of premium. An unhedged short position carries *more* residual risk than that, which is the quantitative case for the defined-risk-only rule we already have. |

**Lessons carried over from equities** — every one of these was a real failure:
- **Phantom equity poisons position sizing.** The 2% cap was computed off
  Alpaca's reported equity, which is inflated ~6x by unadjusted LBGJ. Route
  every risk budget through honest equity.
- **Fail closed on missing data.** A missing price let a $0.03 stock through the
  screener. `passes_risk_filters` now refuses unknown equity and unknown DTE
  rather than assuming a default.
- **Doc/code drift is a real bug, not a typo.** The selection docstring said
  IVR ≥ 50 while the code required 60; the risk docstring said reward/risk 0.20
  while the code enforced 0.25; the monitor said a 200% loss stop while the code
  used 100%.
- **An inverted condition can disable a whole strategy silently.** The theta
  check rejected positive theta while its error message asked for positive
  theta, so no premium-selling position could ever open.
- **A written guardrail that nothing calls is not a guardrail.**
  `check_option_liquidity()` had the right threshold and the right docstring and
  zero call sites. Grep for the caller, not just the function.
- **A per-trade cap does not bound a correlated book.** Five positions each
  capped at 2% is 10% of equity behind one risk factor. The equities side had
  had a correlation check since the beginning; options never got one.
- **Fail loudly on a missing field, not plausibly.** `scenario_analysis`
  defaulted a missing `underlying` to 0, putting every simulated price below
  every breakeven, and reported nine consecutive max-loss scenarios with no
  error. A wrong answer that looks like an answer is worse than a crash.
- **Test the guardrails.** Options had zero test coverage until now; the suite
  is at 122 tests, and each new gate was mutation-tested to confirm the test
  fails when the gate is removed.

## Experiment Timeline
| Phase | Dates | Status | Decision |
|-------|-------|--------|----------|
| Phase 1 — initial paper run | June 16–30, 2026 | Complete | Extended — all realized positions negative; rules overhauled on paper but **never implemented in code** |
| Phase 2 — extended paper run | July 1 – Aug 20, 2026 | **Void — not a valid test** | The overhauled ruleset existed only in this file. Code ran a flat −8% stop, flat 5% sizing, no cooldown, no minimum hold, and an over-counting Judge. Results measure the old ruleset plus defects, not the intended strategy. |
| Phase 2B — first real test of the ruleset | Aug 20 – Sep 3, 2026 | **Active** | Baseline reset 2026-08-20. First period where the documented rules are actually executing. |
| Phase 3 — real money (Robinhood) | TBD | Locked | Unlocks only after Phase 2B criteria are met **and** manual approval |

### Phase 2B baseline — reset 2026-08-20

| Measure | Value at reset |
|---|---|
| Reported portfolio value | $579,586.26 (**inflated — includes phantom LBGJ equity**) |
| Honest portfolio value | **$91,455.93** (LBGJ carried at cost) |
| Honest return since inception | **−8.54%** |
| Cash | $71,258.65 (~78% of honest value) |
| Genuine open positions | AAPL +11.5%, NVDA +13.1%, DDOG +6.8% |
| Suspect position | LBGJ — 154,903 sh, blocked from trading pending reconciliation |
| Prior win rate | 38.9% (7 of 18) — measured on the old code |
| Trades counted toward Phase 2B | **0** — counter starts fresh |

**Why the reset was necessary.** Everything before 2026-08-20 was measured on
code that contradicted this file, so it cannot validate the ruleset:

1. **Judge scores were inflated up to 2.0 points** — criteria were summed past their own weights. Pre-reset scores are not comparable to post-fix ones, so the "avg Judge ≥ 7.0" criterion had no meaning.
2. **Phantom equity distorted everything from 2026-08-03.** An unadjusted reverse split restated LBGJ from $0.02 to $3.79 while share count and basis held, adding ~$488K of unrealizable equity.
3. **The agent was frozen for 2.5 weeks.** That inflated portfolio value pushed cash to a reported 12.3%, tripping the Judge's hard `CASH BELOW 20% FLOOR` veto. **Zero buys placed between 2026-08-03 and 2026-08-20** — every candidate was vetoed. No new calibration data was produced.
4. **Only buys are scored.** `reviewer.py` reviews buy fills only, so a period of net selling generates no calibration rows regardless of outcome quality.

**Decision date: 2026-09-03.** `graduation-decision.yml` runs automatically at
21:30 UTC that day and commits a verdict to
`logs/paper/2026-09-03-graduation-decision.md`. Run it any time with
`python3 reviewer.py graduation`, or manually via workflow_dispatch.

Expect the occurrence count to be the binding constraint: 15 qualifying trades
in ten sessions requires roughly 1.5 buys per day, and the tightened gates
(weight clamp, chase veto, $5 floor, discovery quality veto) deliberately
approve fewer trades. **A short sample is not a pass** — if occurrences fall
short on 2026-09-03, extend rather than graduate.

**Decision log**
- **2026-06-29** — Alex extended by 7 days after all 6 closed positions were negative. Ruleset overhauled in this file. *In hindsight: the overhaul was documentation only; none of it reached the code, so the extension tested nothing new.*
- **2026-08-20** — Phase 2 declared void and baseline reset. Root causes fixed and merged to `master`: Judge weight clamp, $5 price floor, research-vs-execution price check, tiered stops, conviction sizing, 48h cooldown, 30-day minimum hold, phantom-equity detection, real VIX sizing, earnings-watch veto, insider-buy signal. Phase 2B opened through 2026-09-03. Real money remains locked.

## Daily Schedule — SINGLE SOURCE OF TRUTH (all times ET, weekdays only)

**Where automation actually runs:** GitHub Actions, using GitHub Secrets — not on
any local machine, and not from `routines.json`. The `.github/workflows/*.yml`
files are authoritative. `routines.json` is reference material only: no script
reads it, and its times are not what runs.

**Primary trigger as of 2026-10-12: an external cron service, not GitHub's
`schedule:` event.** See "External trigger" below — `schedule:` stays wired
on every workflow as a free fallback layer, but it is no longer what's
relied on to fire on time.

**Four scheduled jobs, not nine.** Each pipeline runs its steps sequentially
inside a single job — the agents are not individually scheduled. Any per-agent
clock time you may remember (8:30 AM intelligence, 10:00 AM judge, 4:15 PM
reviewer) was aspirational and never existed in cron.

**Multiple cron entries per job are redundant attempts, not extra runs.**
GitHub Actions scheduled triggers are not guaranteed to fire on time —
delays of 3.5–9 hours have actually happened on this repo. The morning and
options windows originally cut off at 11:30 AM ET, on the assumption that
delays were occasional and roughly random, so spreading a few attempts across
105 minutes would likely land one inside it. On 2026-09-03, 09-04 and 09-07
GitHub instead delayed the *entire block* of a day's triggers by a similar
multi-hour offset together — every attempt on all three days landed between
1 PM and 3 PM ET, after the cutoff, producing zero real research on three
straight trading days despite every scheduled run showing green. Widened to
4:00 PM ET on 2026-09-08, matching the midday window, which had gotten real
checks in on nearly all of those same delayed days because 6 hours is wide
enough to catch a multi-hour delay landing anywhere inside it. Every extra
cron below sits inside the legal window its job enforces; the first one to
land inside the window runs the pipeline, and every trigger after that is a
free no-op — the morning/EOD/options jobs check an idempotency marker before
doing any work, and the midday job's checks (`check-stops`, `check-phantom`)
are read-check-act operations where running more often is strictly better,
not just delay insurance.

**A second, more specific cause found 2026-09-18: this repo's own crons were
sitting on the most congested minutes on GitHub's infrastructure.** Pulling
the actual fire times for Sept 14–18 showed the real research run landing
4–5 hours after its nominal target *every single day* — not the occasional
delay the widened window was built around, but a near-daily one, with real
research never once landing on the 9:45/10:15/10:45/11:15 AM slots. GitHub's
own scheduled-workflow docs say the schedule event "can be delayed during
periods of high load," and that "high load times include the start of every
hour" — recommending an off-grid minute specifically to avoid it. Every cron
in morning-pipeline.yml, midday-pipeline.yml and eod-pipeline.yml was sitting
exactly on :00/:15/:30/:45 — the same round minutes most other repos'
schedules also default to, i.e. the most congested moments on GitHub's whole
triggering infrastructure. options-pipeline.yml's crons already happened to
avoid that grid (an accident of the "offset 5 minutes from morning" choice),
and options got noticeably fewer complete misses than morning over the same
week — circumstantial, but consistent with the mechanism. All equities crons
were moved off the grid on 2026-09-18 (`:52/:22/:52/:22/:07/:37` for morning,
similar for midday/EOD). This does not guarantee the platform-wide delay
never recurs — it reduces exposure to one *documented* cause of it, it is not
a fix for GitHub Actions scheduling being best-effort in general. The forex
agent's own workflow (`forex-agent.yml`, outside this file's ownership) has
every cron on `:00`, the single worst-documented minute — flagged for that
session, not changed here.

**The retiming fix hit its ceiling on 2026-10-05.** Every cron across all
four equities workflows — including options, already off the congested
grid — landed between 5:14 PM and 10:14 PM ET that day, after market close.
GitHub delayed the *entire block* again, exactly the Sept 3/4/7 failure mode,
proving a within-`schedule:` fix can reduce exposure but can't eliminate it:
`schedule:` is documented as best-effort, full stop, and no cron placement
survives a platform-wide delay that pushes the whole day's block past the
legal window.

### External trigger — the actual fix (2026-10-12)

The only way to stop depending on GitHub's `schedule:` event is to not use
it as the primary trigger. `workflow_dispatch` is a *different* event type —
it is not subject to the "high load" delay `schedule:` carries by its own
documentation, because nothing is polling a cron table on GitHub's side
waiting for a time match; the run is created the instant the API call
lands. Every pipeline here already accepts `workflow_dispatch` (it's how
this file's author manually re-triggered a missed day throughout
Sept–Oct 2026), so the fix is moving who calls it: an external cron service
(outside GitHub entirely) now calls

```
POST /repos/AlexTSung/ai-trading-agent/actions/workflows/{workflow}.yml/dispatches
Authorization: Bearer <fine-grained PAT, Actions: Read and write, this repo only>
Body: {"ref": "master"}
```

**What's actually configured, as of 2026-10-13: hourly, every day, no
market-hours restriction.** The original plan in this section called for a
handful of calls per workflow per day at fixed times mirroring the old
`schedule:` crons. Alex's actual cron-job.org configuration is simpler and
different: one job per workflow firing on an hourly interval, all day, every
day — easier to set up in cron-job.org's UI than four-to-six fixed times, and
confirmed intentional ("I am running the cron jobs every hour on
chron-jobs org. This is expected"). The per-workflow fixed-time list below
the table is now historical — it describes `schedule:`'s fallback crons,
which are unchanged, not the external trigger's actual cadence.

The in-repo `schedule:` crons are left in place on every workflow as a
zero-cost fallback layer — if the external service ever has its own outage,
GitHub's own (less reliable, but free and already written) scheduler still
gets a chance. `GUARDRAIL G1` (idempotency / rolling re-run interval) makes
every one of these calls — scheduled or external, first or thirtieth — a safe
no-op or a fresh useful re-run, never a duplicate trade, so the external
pinger needed no new code to be safe to add at any frequency.

**Why hourly, all day, turned out to be the right call — not just a
tolerated one.** `research.py`'s intelligence/screener/news layers go stale
within a day; a once- or twice-daily trigger was discarding most of that
staleness for free. The cost is GitHub Actions minutes on this private repo's
metered quota (billed per job regardless of how fast a guardrail skips it) —
real, but one Alex has explicitly accepted in exchange for continuous
scanning of current positions and candidate tickers. See `run_github.sh`'s
GUARDRAIL G1 comment and EQUITIES_CHANGELOG.md's v1.4.0 entry for the concrete
incident (2026-10-13) that the old one-shot-per-day lock caused: three
Judge-APPROVEd candidates (NVDA, MSFT, NU) scored off a midnight run and never
reached the executor, because every later trigger that day — including every
one during actual market hours — saw the day already "handled" and skipped.

**Setup is intentionally not something this session could do alone.** It
needs a GitHub fine-grained personal access token — an account-level
credential only the repo owner can mint — stored as a secret on whichever
external cron service is chosen. Neither the token's creation nor its
storage on a third party happens inside this repo or this session.

| ET | Workflow / runner | Cron (UTC) | Steps, in order |
|---|---|---|---|
| **Hourly, all day, every day** (external trigger) — `schedule:` fallback crons: 9:52 AM + 5 backups to 2:37 PM ET | `morning-pipeline.yml`<br>`run_github.sh` | `52 13`, `22 14`, `52 14`, `22 15`, `7 17`, `37 18 * * 1-5` | 1. `intelligence.py morning` — VIX, fear/greed, macro, global, earnings watch, insider buys<br>2. `reddit_screener.py scan` — StockTwits trending<br>3. `screener.py scan` — S&P 500 + Nasdaq 100 + news firehose<br>4. `research.py full` — bars, news, MAs, RSI on watchlist + discovered tickers<br>5. `judge.py session` — score with intelligence context, APPROVE/HOLD/VETO<br>6. `trade.py session` — execute approved trades only, and only while the market is actually open |
| **9:40 AM** (+4 backups to 2:35 PM) | `options-pipeline.yml`<br>`run_options_github.sh` | `40 13`, `10 14`, `10 15`, `5 17`, `35 18 * * 1-5` | `run_options.py` — manage existing positions, then screen the options watchlist for new defined-risk trades. Same 9:35 AM–4:00 PM ET window as equities morning. IV rank and the chain are freshest right after open, and widening this window trades some of that freshness for reliability — see run_options_github.sh's GUARDRAIL G2 comment. Existed only as a local `run_options*.sh` tied to one machine before this. Already off the :00/:15/:30/:45 grid, so left unchanged in the 2026-09-18 retiming. |
| **10:37 AM, 12:07 PM, 2:07 PM** | `midday-pipeline.yml`<br>`run_midday_github.sh` | `37 14`, `7 16`, `7 18 * * 1-5` | 1. `trade.py check-phantom` — corporate-action audit (Hard Rule 11)<br>2. `trade.py check-stops` — tiered stops + inverse ETF max-hold<br>3. `research.py midday` — signal reversals |
| **5:22 PM** (+1 backup at 6:07 PM) | `eod-pipeline.yml`<br>`run_eod_github.sh` | `22 21`, `7 22 * * 1-5` | 1. `reviewer.py eod` — post-trade scoring + calibration log<br>2. `journal.py eod` — synthesize the daily journal<br>*(1hr post-close buffer so closing prints settle)* |
| **Sep 3, 5:30 PM** | `graduation-decision.yml` | `30 21 3 9 *` | `reviewer.py graduation` — Phase 2B verdict. Advisory only; manual approval still required. |

Guardrails inside the runners: weekends skip; the midday job runs only
between 10:00 AM and 4:00 PM ET; the EOD job refuses to run before 4:00 PM
ET. The morning job (`run_github.sh`) no longer has a time-of-day cutoff —
removed 2026-10-13 along with the one-shot daily lock, see below — and the
options job still aborts after 4:00 PM ET. Every one of these is computed
from the **real US/Eastern wall-clock time** (`TZ="America/New_York" date`,
or `market_time.py` in the Python layer) rather than the runner's UTC clock
compared against a fixed offset — the earlier UTC-offset version broke the
instant a delayed run crossed midnight UTC, and separately drifted an hour
every time DST changed twice a year. `workflow_dispatch` with `test_mode:
true` bypasses the time windows on the midday and options jobs (and, as of
2026-10-13, has no effect on the morning job — kept only for input parity).

**Morning job idempotency is now a 50-minute rolling interval, not a daily
lock.** Through v1.3.1, GUARDRAIL G1 skipped the rest of the day the instant
`logs/paper/$DATE.json` existed at all — correct when `schedule:` fired only
a handful of times a day, wrong the moment the external trigger started
firing hourly around the clock by design. On 2026-10-13 the only research run
of the day landed at 12:01 AM ET (`market_open: false`); the Judge scored
three APPROVEs (NVDA, MSFT, NU) off that midnight-stale pass, and every later
hourly trigger during actual market hours skipped outright because the file
already existed — those approvals never reached the executor. `run_github.sh`
now re-runs whenever `$DATE.json` is missing **or** older than 50 minutes, so
an hourly external trigger produces an hourly real pipeline run. This cannot
cause a duplicate buy: `trade.py`'s buy path already requires `sym not in
open_syms`, so a symbol bought earlier the same day is not re-entered by a
later run that same day. Trading-hours safety was never this guardrail's job
and is unaffected — `trade.py session` reads `research.get("market_open")`,
sourced from Alpaca's real-time clock at the moment research last ran, and
refuses to place any order when it is false, confirmed directly in
2026-10-13's job logs (`{"status": "MARKET_CLOSED", "no_trades": true}`).

Local equivalents for manual runs — these do **not** replace the schedule, they
just let you inspect state:

```
python3 research.py full        python3 trade.py check-stops
python3 trade.py session        python3 trade.py check-phantom
python3 journal.py eod          python3 reviewer.py graduation
```

## Two Agents, Two Judges — Never Share Scoring State

This repo hosts two independent trading agents. They share infrastructure
patterns but **must not share scoring state or learned parameters**.

| | Alpaca equities | OANDA forex |
|---|---|---|
| Judge | `judge.py` | `forex_judge.py` |
| Criteria | technical, news, macro, portfolio, volatility, quality | carry, momentum, technical, session, volatility, risk_reward |
| Executor | `trade.py` | `forex_trade.py` |
| Learned params | `logs/learnings/alpaca_judge_hints.json` (`scope: alpaca_equities`) | `forex_config.json` |
| Logs | `logs/paper/` | `logs/forex/` |

Only `technical` and `volatility` even share a name, and they measure different
things. A carry score is meaningless for AAPL; an RSI-on-daily-bars reading does
not transfer to a 4-hour FX chart. Applying one agent's learned weights to the
other would silently corrupt both.

**Enforcement.** Every hints file carries a `scope` tag and the consuming Judge
refuses a file whose scope is not its own. Verified: `forex_judge.py`,
`forex_optimizer.py` and `forex_trade.py` contain zero references to
`alpaca_judge_hints.json` or `logs/learnings/`, and `judge.py` contains zero
references to `forex_config.json` or `logs/forex/`.

### Learned hints — four rails
`learning_agent.py` proposes adjustments after each EOD. `judge.py` consumes
them only under all four of:

1. **Scope** — file must be tagged `alpaca_equities`.
2. **Graduated sample** — the *size* of any adjustment scales with the number of
   **closed round trips** (see below). Calibration rows are not enough; an
   entry-day score never saw its own exit.
3. **Tighten-only** — a learned gate may rise above the documented floor, never
   fall below it. A loop that can lower its own bar will do exactly that to book
   more apparent wins.
4. **Clamp + log** — weight multipliers bounded by the tier band, and every
   applied hint is printed in the judge report each run, applied or not.

### Learning cadence — observe fast, adjust in proportion

| Closed round trips | Tier | Weight band | What happens |
|---|---|---|---|
| every trip | — | — | pattern recorded, printed by `learning_agent.py` |
| 1–4 | `observe` | none | nothing applied |
| 5–9 | `advisory` | ±3% | risk-**reducing** gate changes apply; weights advisory only |
| 10–19 | `active` | ±6% | small weight nudges take effect |
| 20–29 | `active` | ±10% | |
| 30+ | `confident` | ±15% | full band |

**Observation is separated from adjustment on purpose.** Recording what each
round trip implies costs nothing and builds the record. Changing a parameter on
that same evidence is what overfits. So the agent notices immediately and moves
slowly.

**The asymmetry is deliberate.** Risk-*reducing* changes (raising a gate) apply
from the advisory tier, because reacting quickly to losses is prudent.
Risk-*increasing* changes need more evidence, because reacting quickly to wins
is how a run of luck becomes policy.

**Why not act hard at 5 trips.** The judge report prints a Wilson 95% confidence
interval next to every hint. At 5 round trips a 60% observed win rate spans
**23%–88%** — an interval containing both "excellent" and "losing". At 30 it
narrows to 42%–75%. Small nudges on thin evidence are fine; betting the strategy
on it is not. A raw −50% weight hint is throttled to −6% at 10 trips and only
reaches −15% at 30.

`learning_agent.py` may append to the **Lessons Learned** section of this file
and nothing else. It cannot edit Hard Rules or graduation criteria.

### File ownership
Two agents have already collided on these files, silently reverting safety
controls. Whoever edits a file outside their own column above must **rebase, not
regenerate** — and run the guardrail checks before pushing.

## Hard Rules (Never Break These)
1. **Check market status first.** Never place orders when `is_open` is false.
2. **No market orders. Buy pullbacks, not breakouts.** Entry limit orders must target MA20 support levels or RSI < 45 conditions — not just ask − 0.3%. A cosmetic 0.3% discount on a stock at a 20-day high is still a bad entry. Wait for the stock to pull back to a natural support level before entering. Formula when entering: `limit = MA20 × 0.995` (0.5% below MA20 support) for pullback entries, or `ask × 0.997` only when the stock is already below MA20 (value entry).
3. **Stop-loss — tiered by stock quality:**
   - **Tier 1 — blue-chip compounders** (AAPL, MSFT, GOOGL, AMZN, NVDA): 20% stop OR thesis-based sell — whichever comes first. Price dips alone are NOT a sell trigger. Only exit if MA20 crosses below MA50 AND confirmed by a negative fundamental catalyst (earnings miss, guidance cut, leadership change).
   - **Tier 2 — growth/mid-cap** (DDOG, TSLA, QCOM): 12% stop. These are higher-beta names where 8–10% swings are normal.
   - **Tier 3 — speculative** (NOK, PYPL and any stock with market cap < $10B): 10% stop.
   - Log every stop-loss as a STOP-LOSS event with the tier and reason.
4. **48-hour re-entry cooling-off.** After a stop-loss triggers on any symbol, do not re-enter that same symbol for at least 48 hours (2 trading days). The previous pattern of stopping out and immediately rebuying the same stock at nearly the same price locks in realized losses with no benefit.
5. **Minimum hold period.** For any position with Judge score ≥ 8.0, the minimum hold is 30 calendar days unless: (a) stop-loss triggers, (b) a confirmed negative fundamental catalyst emerges, or (c) MA20 crosses below MA50. Do not exit based on short-term price noise.
6. **Position size — scaled to conviction:**
   - Judge score 9.0–10.0: up to 8% of portfolio
   - Judge score 7.5–8.9: up to 5% of portfolio
   - Judge score 7.0–7.4: up to 3% of portfolio
   - Never size down from a winning position solely because it exceeded the initial % cap via gains.
7. **Partial profit-taking at T1.** When a position reaches T1 (entry + 2× stop distance), sell 40% of shares. Let the remaining 60% run toward T2 with a trailing stop set at entry price (breakeven). This ensures the system takes gains, not just stops.
8. **Cash reserve**: Always maintain at least 20% of portfolio value in cash. Do not deploy capital that would breach this floor.
9. **No earnings week FOMO**: Do not open new positions in a stock within 3 trading days before its earnings report.
10. **Journal is mandatory**: Write a journal entry every market day, even if you placed no trades. A day with no journal entry is a bug.
11. **Phantom equity is never performance.** If a position's price moves ≥10× (or ≤1/10×) against its cost basis while the share count is unchanged, treat it as an unadjusted corporate action — not a gain. Such a position is blocked from all trading, excluded from exit placement, and its P&L is logged as `DATA_SUSPECT` rather than counted toward win rate or graduation. Reconcile with the broker before treating the value as real.
    - **Precedent**: on 2026-08-03 LBGJ was restated from $0.02 to $3.79 over a weekend while the position held 154,903 shares at a $0.0288 basis throughout. Reported portfolio value jumped from $86,925 to $674,658 — roughly $488,000 of equity that did not exist. A genuine reverse split reduces shares and raises basis in the same ratio, leaving market value approximately flat.
    - Run `python3 trade.py check-phantom` to audit open positions at any time; the midday pipeline runs it automatically.

## Macro Regime Check (Dalio)
Before any trading session, note the macro regime:
- SPY MA20 vs MA50: above = risk-on, below = risk-off
- In risk-off: reduce max long position size from 5% to 2.5%, require STRONG_BUY only
- In risk-on: normal rules apply

**Now enforced in code.** `judge.py macro_regime()` derives `RISK_ON` /
`RISK_OFF` / `NEUTRAL` from the same branch `score_macro()` scores, so the
score, the rationale string and the position size cannot disagree. It is passed
to the executor, where `trade.regime_adjusted_position_pct()` applies the 2.5%
ceiling and `buy_with_guardrails` blocks anything weaker than STRONG_BUY.

Until 2026-08-26 **neither half existed.** `score_macro` returned 0.0 and
printed `(half sizing applies)`, but nothing read the regime — `size_multiplier`
came only from the VIX ladder — so the Judge announced a risk control that was
never applied and the book ran full conviction size into a downtrend. A message
claiming a limit it does not enforce is worse than no message: it reads as
protection in the log while the exposure is unchanged.

### How the risk reducers compose

Order matters, and **every step may only lower the number**:

| Step | Rule | Effect |
|---|---|---|
| 1 | Conviction (Hard Rule 6) | 8% / 5% / 3% by Judge score |
| 2 | VIX ladder (Citadel) | × 0.5 above VIX 25, blocked above 35 |
| 3 | Risk-off ceiling (Dalio) | `min(result, 2.5%)` |

Step 3 is a **`min`, not an assignment**. If the VIX ladder has already cut a 3%
position to 1.5%, entering risk-off must not raise it back to 2.5% — combining
two risk reducers can never increase exposure.

**The naive form is wrong and is guarded by a test.** Writing
`size = capital × multiplier` capped at 2.5% conflates a *multiplier* (0.5–1.0)
with a *fraction of capital*: it computes 50–100% of the book, so the 2.5% cap
binds in every case and flattens the 8/5/3% tiers to one size for every
trade — including in risk-on, where the Dalio ceiling should not apply at all.

Measured on honest equity of $91,118.79:

| Judge score | Conviction | Risk-on | Risk-off |
|---|---|---|---|
| ≥ 9.0 | 8% | $7,290 | **$2,278** |
| 7.5–8.9 | 5% | $4,556 | **$2,278** |
| 7.0–7.4 | 3% | $2,734 | **$2,278** |

**Known limitation — the regime signal is noisy.** MA20 vs MA50 flips on any
crossing however small. On 2026-08-26 SPY's MA20 was $745.87 against an MA50 of
$745.02 — a **0.11%** gap deciding whether the entire book sizes at 8% or 2.5%.
A 200-day SMA confirmation or a hysteresis band would make the regime stable;
neither is implemented. VIX is also a *lagging* volatility measure — it spikes
during and after a decline, not before — so both regime inputs are reactive.

## Bearish Market Playbook
When the market is in a downtrend (SPY MA20 < MA50), don't sit in cash — deploy bearish instruments.

### Three Bearish Tools (in order of preference)
1. **Inverse ETFs (SQQQ / SPXS / SOXS)** — simplest, capped downside
   - SQQQ: Buy when QQQ MA20 < MA50 AND tech is STRONG_SELL
   - SPXS: Buy when SPY MA20 < MA50 AND VIX > 20
   - SOXS: Buy when NVDA + INTC + MU all STRONG_SELL
   - Max size: 5% of portfolio per position
   - Stop-loss: 5% (tighter than longs — volatility decay compounds losses fast)

   **⚠️ Inverse ETF Hard Rules — Never Break These:**
   - **Do NOT hold long-term.** SQQQ/SPXS/SOXS lose value over time due to daily compounding and volatility decay. The underlying indices have a historical upward bias. These ETFs frequently require reverse splits. They are not investments — they are tactical tools.
   - **Max hold: 3 trading days.** Best used intra-day or for 1–3 days during a sharp confirmed downtrend. Exit before the weekend — weekend gap risk compounds the decay problem.
   - **Strict stop-loss required.** An unexpected market rally will erode the position faster than a normal stock. Set the stop at entry minus 5% and honor it without exception. Never let a losing inverse ETF trade ride.
   - **Choppy markets destroy these positions.** SQQQ delivers -3× the index's *daily* performance. If the market moves sideways or chops up and down over a week — even if it ends flat or slightly down — you can still lose money due to volatility drag. Only enter on a confirmed, directional downtrend, not uncertainty.
   - **Size down in choppy conditions.** If VIX > 30 (high volatility, whipsaw risk), cut inverse ETF size to 2.5% max. Volatility cuts both ways on leveraged instruments.

2. **Short selling individual stocks** — higher conviction, higher risk
   - Trigger: STRONG_SELL signal + clear negative catalyst (index removal, earnings miss, guidance cut, lawsuit)
   - Only on liquid large-caps (avg daily volume > 5M shares)
   - Max size: 2.5% of portfolio (half of normal — unlimited loss exposure)
   - Hard cover: stock moves +5% against you → cover immediately (tighter than 8% long stop)
   - Use `place_stock_order` with `side: "sell"` on a stock not in portfolio
   - Example catalyst: VZ removed from Dow Jones + Q2 loss guidance = ideal short setup

3. **Cash + inverse ETF combo** — risk-off regime play
   - Hold 40% cash + 10% inverse ETFs (SQQQ + SPXS) when VIX > 25
   - No new long positions until VIX < 20 and SPY reclaims MA20

### Bearish Judge Scoring (short setups)
Same 5 criteria, inverted:
- Technical (3.0): STRONG_SELL = 3.0, WEAK_SELL = 1.5, anything else = 0
- News (2.5): negative catalyst quality (index removal, guidance cut, lawsuit = 2.5)
- Macro (1.5): risk-off regime score
- Position (1.0): not already short, short exposure < 10% of portfolio
- Volatility (1.0): liquid, large-cap only
- APPROVE ≥ 7.0, HOLD ≥ 5.0, VETO < 5.0 (same thresholds)

### Discovery — Morning Scan (runs before Researcher)
Every morning before market open:
1. `get_most_active_stocks` — top 10 by volume
2. `get_market_movers` stocks — top 5 gainers AND top 5 losers
3. Filter gainers: price > $5, move < 8% (pump check), market cap > $500M
4. Filter losers: price > $5, move > -3%, check for negative catalyst → potential short
5. Pass qualified tickers to Researcher alongside watchlist
6. Social screener (StockTwits) tickers also feed into Researcher — not just reported

## Signal Hypothesis Log (D.E. Shaw)
Current active signals and their hypotheses:
- MA20/MA50 crossover: trend persistence predicts near-term continuation
- [Add new signals here with written hypothesis before activating]

## Volatility Position Sizing (Citadel)
If VIX > 25: multiply all position sizes by 0.5
If VIX > 35: no new positions, manage existing only

**Now enforced in code.** The VIX level comes from FRED `VIXCLS` via
`intelligence.py get_fear_greed()` (misleadingly named — it returns the real VIX
close, not a sentiment index). `research.py load_intelligence_context()` carries
it into the research output, `judge.py score_volatility()` scores against it, and
`trade.py` applies the resulting `size_multiplier` to actual share counts.
Above VIX 35 the Judge issues a hard `VOLATILITY VETO`.

Note: `intelligence.py get_vix()` returns a **VIXY share price**, which is not
comparable to a VIX index level. Do not use it for these thresholds.

## Intelligence Factors (what the morning scan feeds into decisions)
`intelligence.py morning` writes `logs/paper/YYYY-MM-DD-intelligence.json`.
These fields are consumed as decision factors:

| Field | Source | Used for |
|---|---|---|
| `fear_greed.vix` | FRED VIXCLS | Volatility score, size multiplier, VIX>35 veto |
| `fear_greed.score` / `rating` | derived from VIX | Regime context in rationale |
| `earnings_watch` | Alpaca news keyword scan | 3-day earnings blackout (Hard Rule 9) |
| `insider_activity` | SEC EDGAR Form 4 | +0.5 quality score for recent insider buys |
| `market_discovery` | Alpaca most-active / movers | Discovery tickers for the Researcher |

**Known limitation — earnings.** Alpaca has no earnings endpoint on the free
tier, so both `intelligence.py get_earnings_calendar()` and the Judge's own
check are keyword scans over news, not a real calendar. The Judge takes the
union of both for recall and ignores purely backward-looking recaps ("reported
Q2 results") to cut false positives. A dedicated calendar feed would be strictly
better and is the recommended next upgrade; until then the blackout is
best-effort and can miss a report that generated no news.

## Correlation Check (Bridgewater)
Before buying a new position, confirm it is not highly correlated
with an existing holding. SPY and QQQ count as one position for
correlation purposes.

## Decision Framework
Before placing any trade, answer all of these:
1. Is the market currently open?
2. What is my current cash balance? Will this trade respect the 20% cash reserve?
3. What positions are already open? Am I adding to a winner or a loser?
4. What does the 20-day MA vs 50-day MA say? (20 > 50 = bullish, 20 < 50 = bearish)
5. What does recent news say? Is there a catalyst or a risk event?
6. What is my downside if this trade hits the stop-loss? Is that acceptable?
7. What would I need to see to be proven wrong on this trade?

## Trade Sizing Formula
```
# Conviction-scaled sizing
if judge_score >= 9.0:   pct = 0.08
elif judge_score >= 7.5: pct = 0.05
else:                    pct = 0.03

max_position_value = total_portfolio_value * pct
shares_to_buy = floor(max_position_value / limit_price)
```
Never round up. Always round down to whole shares.

## Entry Checklist (run before every order)
Before placing any buy:
1. Is the stock at or below MA20? If above MA20 by more than 3%, wait for a pullback — do not chase.
2. What is RSI(14)? If RSI > 60, the stock is overbought — wait. If RSI < 45, proceed.
3. What is the stop distance? Stop = entry × (1 - stop_pct_by_tier). Is the risk/reward ratio ≥ 2:1 (gain to T1 vs loss to stop)?
4. Has this symbol been stopped out within 48 hours? If yes, skip.
5. Is earnings within 3 trading days? If yes, skip.

## Exit Playbook
- **At T1** (entry + 2× stop distance): sell 40% of shares, move stop to breakeven on remainder.
- **At T2** (entry + 3× stop distance): sell another 40%. Let final 20% run with trailing stop.
- **Thesis break** (Tier 1 stocks only): MA20 crosses below MA50 AND negative fundamental catalyst → sell full position.
- **Stop triggered**: exit full position, log STOP-LOSS, wait 48h before re-entry.
- **Never** sell a quality compounder (Tier 1) solely because of a market-wide dip or short-term price decline.

## Technical Signal Interpretation
- **Strong Buy**: 20-day MA > 50-day MA AND price > 20-day MA AND positive news catalyst
- **Weak Buy**: 20-day MA > 50-day MA AND price between MAs
- **Hold**: No clear signal or mixed signals
- **Weak Sell**: Price < 20-day MA AND 20-day MA declining
- **Strong Sell**: Price < 50-day MA OR stop-loss triggered

## Journal Format
Every journal entry must follow the template in `journal-template.md`.
Sections: Portfolio Status → Market Research (per symbol) → Trades Executed → Positions Closed → Reflection → Tomorrow's Watch

## Lessons Learned
*(Agent updates this section as patterns emerge from journal review)*
- [2026-08-31] **discovery_channel_silently_dead**: `_extract_discovery_tickers(intel_path)` referenced `max_tickers` at `return tickers[:max_tickers]` without ever receiving it as a parameter — every call raised `NameError`, caught by a broad `except Exception`, and returned `[]`. Alpaca most-active, top gainers, and Reddit trending — the entire discovery channel fed by the morning intelligence scan — contributed exactly zero tickers to the Judge, every single day, with no error surfaced anywhere the journal or logs would show it. Found while widening the discovery funnel for more trade occurrences; the funnel had a hole in it, not just a narrow opening. Fixed: the parameter now exists, per-source slices widened to match what `get_market_discovery()` already fetches, and sources are interleaved round-robin rather than concatenated-then-truncated — the latter meant a full most-active batch alone could still crowd out every gainer and Reddit ticker once its slice was widened. **The general lesson, again**: a broad `except Exception: return []` at the boundary of a function is exactly where a real bug goes to hide — it looks like "gracefully handling missing data" in the code and reads as "no candidates today" in the log, and those are indistinguishable without instrumentation that says which one happened.
- [2026-08-31] **naive_clock_mislabelled_as_ET**: journal.py/reviewer.py/research.py/judge.py/trade.py/learning_agent.py used `date.today()`/`datetime.now()` for filenames and printed the result labelled "ET" — but that reads the runner's clock, UTC on GitHub Actions. On 2026-08-28 a delayed EOD run (actual time 11:09 PM ET) filed itself as `2026-08-29.md` and printed "Generated: 2026-08-29 03:09:48 ET", a UTC timestamp one day off and falsely labelled. The bash guards were already fixed to compute in `America/New_York`; the Python layer underneath them was not, so the mismatch just moved down a layer. Fixed via a shared `market_time.py` (`et_today()`/`et_now()`, zoneinfo-based) used everywhere "today" determines a filename or cross-script lookup. Left untouched on purpose: self-consistent naive pairs (48h cooldown timestamps, min-hold entry dates, client-order-id uniqueness suffixes) where both the write and the read use the same naive clock — touching only one side there would raise `TypeError: can't subtract offset-naive and offset-aware datetimes`, a new crash for no benefit.
- [2026-08-24] **high_score_poor_outcome** (seen 2x): Judge score ≥8.0 but outcome POOR_TRADE (2x: AAPL, AAPL). Judge may be overweighting news sentiment. Consider raising APPROVE threshold to 8.0.
- [2026-08-24] **persistent_judge_overconfidence** (seen 5x): Judge consistently scores -4.8 pts above reviewer (5 trades). Judge is overconfident. Recommend reducing news component weight by 10%.
- [Date initialized] Beta version launched. All lessons TBD.
