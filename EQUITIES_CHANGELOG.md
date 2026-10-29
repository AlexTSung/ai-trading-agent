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

## [v4.1.0] — 2026-10-29

**MINOR** — Two real findings surfaced while actually building the v4.0.0
local scheduler, both caught before a single real order had been placed.

**Finding 1 — a genuine double-buy risk from v3.0.0+v4.0.0's interaction.**
`build_buy_candidates()` only ever checked `positions` (filled holdings).
Under v3.0.0, a BUY can sit in Robinhood's Trade Approval queue, placed but
unfilled, for longer than v4.0.0's own ~30-minute re-run cadence. It isn't
a position yet, so the next tick wouldn't see it as held and would build a
second candidate for the same symbol — a real double fill if both ever got
tapped approved. Same bug class the v1.5.1 `open_buy_order_syms` fix closed
on the Alpaca side. Alex: "I don't need to use the Alpaca MCP that was for
fake money... I need to use Robinhood MCP" — which also resolved Finding 2.

**Finding 2 — step 1a's market-open check depended on the wrong broker.**
Alpaca's `get_clock` was used because it already existed and worked, not
because Robinhood trading has anything to do with Alpaca's credential
health. When Alpaca MCP returned 401 (unrelated local credential problem),
step 1a could never pass, blocking every Robinhood tick all day with no
trade ever attempted. Alex's framing was exactly right: the real-money
pipeline shouldn't depend on the fake-money broker's connection for
anything, including a fact as broker-agnostic as "is NYSE open."

### Strategy / CLAUDE.md changes
- Step 1a rewritten: uses `market_time.is_nyse_session_open()` instead of
  Alpaca's `get_clock`, with the incident stated directly so nobody
  reintroduces the Alpaca dependency later believing it's harmless.
- Step 3: open orders are now a required fetch alongside account/positions,
  not optional.
- Step 7: `build_buy_candidates()` call updated to pass `open_orders`,
  marked required (not optional) given v3.0.0+v4.0.0 are both in effect.
- New addendum to "Robinhood Scheduled Execution — v4.0.0" documenting
  concrete setup findings from actually building the scheduler: permission
  configuration lives in the local CLI's own settings (not something a chat
  session grants); the working headless-invocation flags
  (`-p --permission-mode dontAsk --allowedTools ... --disallowedTools ...`,
  no blanket skip-permissions flag needed); running preflight checks from a
  neutral directory to avoid paying ~$0.07-0.18/call to load this file
  every ~30 minutes; `logs_dir` is the private repo's `logs/` subdirectory;
  launchd does not fire while the machine sleeps, and a missed tick is not
  queued or caught up.

### Code changes
- `robinhood_trade.py`: `build_buy_candidates()` gains an `open_orders`
  parameter (default `None`, safe) and a new `OPEN_ORDER_STATUSES` set.
  Skips any symbol with an existing non-terminal buy order. No
  `client_order_id` prefix matching needed, unlike the Alpaca-side fix --
  the Robinhood MCP account is dedicated solely to this agent (confirmed:
  the only tradable account is the "MCP" account), so every order on it is
  already this pipeline's own.
- `market_time.py`: new `is_nyse_session_open()`, `NYSE_HOLIDAYS`, and
  `NYSE_EARLY_CLOSE` (2026-2027 populated; 2027 H2 early-closes need
  checking against NYSE's actual calendar before relying on them). Fully
  self-contained — ET clock plus a hardcoded calendar, no broker API call,
  so it can never 401. An unlisted year degrades to a plain weekday+hours
  check rather than refusing to run, since the failure mode of a missing
  holiday (a harmless rejected order on an actual holiday) is far less
  severe than a missing early-close (trading attempted 2-3 hours into an
  already-closed session) -- both are handled for 2026, only the holiday
  list extends cleanly past it.
- `tests/test_robinhood_trade.py`: 5 new tests for the `open_orders` check
  (pending buy blocks, filled/cancelled/rejected/expired don't, pending
  sell doesn't, different-symbol pending buy doesn't, missing `open_orders`
  defaults safely). Mutation-tested: removing the check fails
  `test_pending_buy_order_blocks_a_second_one`.
- `tests/test_guardrails.py`: 11 new tests for `is_nyse_session_open()`
  (regular hours, both boundaries, weekend, 2026 Thanksgiving/Christmas,
  both 2026 early-close days including the specific case of the v4.0.0
  afternoon window overlapping one). Mutation-tested: removing the holiday
  check fails 2 of the 11.

### Comparability
- Does this invalidate the prior performance record? **No.** No
  scoring/sizing/paper-book change. Both fixes close gaps in a book that
  has not yet placed a single real trade.


## [v4.0.1] — 2026-10-29

**PATCH** — Alex asked whether the new Robinhood scheduling (v4.0.0)
accounted for the existing equities `morning-pipeline` cron-job.org
schedule. It did implicitly — step 6 reads whatever `judge.json`
`morning-pipeline` already produced — but nothing checked *how stale* that
file actually was before acting on it. If that cron ever misses a beat
(this file documents GitHub `schedule:` delays of 3.5-9 hours happening on
this exact repo), the Robinhood job would have silently used however-old a
Judge score it found. Restores the same fail-closed-on-stale-data
discipline already applied everywhere else in this file — the intent was
always this; step 6 just never said so.

### Strategy / CLAUDE.md changes
- Execution Procedure step 6: explicit 90-minute staleness check on
  `judge.json`'s embedded `timestamp` (1.5x `GUARDRAIL G1`'s 50-minute
  threshold, to allow for this procedure's own ~30-minute check cadence
  layered on top). Stale → skip this run's buys entirely and log why,
  rather than size a trade off a Judge score that may no longer reflect
  current conditions. Also states explicitly, for clarity: midday-pipeline
  and eod-pipeline are unrelated to this procedure — Robinhood's own
  stop-loss/phantom checks (step 5) query live Robinhood positions
  directly via MCP, not anything those two pipelines produce.

### Code changes
- None. Procedural clarification only, same as v3.0.0/v4.0.0 — the
  staleness check is implemented as part of the interactive/scheduled
  session's own step 6 logic, not a separate module.

### Comparability
- Does this invalidate the prior performance record? **No.** No
  scoring/sizing/paper-book change; this only tightens when the real-money
  book is willing to act.


## [v4.0.0] — 2026-10-29

**MAJOR** — Alex: "I want this to automatically suggest trades every day
that the stock market is open from 8:30AM to 11:30AM CST for morning
session trades and 1:00-3:00pm CST for afternoon trades." This overrides
the Robinhood Execution Procedure's own "never from an unattended
workflow... structural, not a preference" line — a second, separate
structural loosening on the same day as v3.0.0, not a continuation of it.
I recommended against this specifically: one manual, watched trade first,
given the pipeline had placed exactly zero real Robinhood trades up to
this point. Alex chose full scheduled automation instead. Baseline does
not reset — this is an execution-authorization change for the real-money
book only.

### Strategy / CLAUDE.md changes
- **Robinhood Execution Procedure** intro rewritten: no longer requires an
  interactive session; may run unattended on a local schedule. New step 1a
  added — verify the market is genuinely open via Alpaca's `get_clock`
  (account-agnostic, Hard Rule 4), not just "the cron fired inside the
  window," since a scheduled window can span a market holiday a calendar
  can't know about on its own.
- New section **"Robinhood Scheduled Execution — v4.0.0"**: the two
  windows in Central Time (explicitly labeled CT throughout — the one
  place in this file that isn't ET, called out the same way every other
  zone-labeling discipline here has been, after two prior incidents in
  this exact codebase from an unlabeled or mislabeled clock); the decision
  to re-run every ~30 minutes within each window rather than once (mirrors
  the existing equities hourly-cadence reasoning, and is safe because
  `build_buy_candidates()` already skips symbols already held); why this
  has to be a local cron/launchd job and categorically cannot run on
  GitHub Actions (the `robinhood-trading` MCP credential lives only in
  Alex's local `~/.claude.json`, authenticated to that specific client);
  the requirement that failures be logged loudly, not silently, given
  nobody is watching in real time; and the still-open question this
  surfaces — if Trade Approval is a push-notification-and-tap control,
  "unattended" and "a human taps approve" are in tension, and this has not
  been observed to resolve either way yet.
- New **Decision log** entry (Experiment Timeline), same format as the
  prior two, explicit that this is a second, distinct override from
  v3.0.0's same-day entry, with my recommendation against it stated
  alongside Alex's choice to proceed.

### Code changes
- None. Same as v3.0.0 — this is a procedural/scheduling change. The
  actual cron/launchd job and headless Claude Code CLI invocation are
  being set up on Alex's local machine, outside this repo, by a session
  with the filesystem and `robinhood-trading` MCP access to do that
  correctly and verify it.

### Comparability
- Does this invalidate the prior performance record? **No.** Nothing about
  scoring, sizing, or the paper book changed. Phase 2B's record stands
  exactly as it did before this entry.


## [v3.0.0] — 2026-10-29

**MAJOR** — Alex: "Yes change the architecture... I want this agent to send
orders directly to Robinhood... I already have the safeguard in place on
the Robinhood side... This is my decision." The Mandatory Confirmation
Protocol's chat-confirmation step is removed for Robinhood orders built
through the documented Execution Procedure, substituting Robinhood's own
Trade Approval (confirmed ON by Alex via the `robinhood-trading` MCP tool)
as the human check. Any Hard Rule loosened is MAJOR per this file's own
versioning table — **baseline does not reset**: this is an execution-
authorization change for the real-money book only; no Judge scoring,
sizing formula, or paper-book logic changed, and Phase 2B's record is
unaffected.

This is the second explicit, named, accepted-risk decision in three days —
same pattern as the 2026-10-27 Phase 3 graduation-gate override: Alex
stated the tradeoff directly rather than it being quietly loosened, and it
is documented as a real decision, not routed around via an informal prompt
to a terminal session (which was the original ask — see the conversation
that led here).

### Strategy / CLAUDE.md changes
- **Mandatory Confirmation Protocol** (Account Guardrails) rewritten to
  scope its "no exceptions" to what it's always actually meant: Alpaca
  paper orders never had a human in the loop (always autonomous via the
  scheduled pipeline); Robinhood orders *through the documented pipeline*
  now rely on Trade Approval instead of a chat reply; any other, ad hoc
  real-money instruction — not built through
  `robinhood_trade.build_buy_candidates()` — still requires the full
  original protocol, with no exception. This scoping was my choice in
  drafting the proposal, not something Alex specified verbatim — an order
  nobody's guardrail code has sized or cooldown-checked is exactly the
  case the protocol still exists to catch.
- **Hard Rule 1** (Account Guardrails) rewritten to match: calling
  `mcp__robinhood-trading__place_equity_order` without a chat confirmation
  is permitted only for pipeline-built candidates, only after Trade
  Approval is confirmed ON that session.
- **Robinhood Execution Procedure step 8** rewritten: `confirmation_prompt()`
  is now logged into the day's journal entry as an audit record of what was
  proposed and why, rather than printed and waited on. The order is placed
  immediately after. Step 1 (verify Trade Approval ON, don't trust a prior
  session) is unchanged and is now the load-bearing safeguard, called out
  explicitly as such.
- New section **"Robinhood Automated Execution — v3.0.0"**: full rationale,
  what changed, what explicitly did not (every guardrail in
  `robinhood_trade.py` — sizing, stop tiers, cooldown, cash reserve,
  phantom-equity detection, no-averaging-down, RISK_OFF ceiling), and the
  accepted residual risk in plain terms — Trade Approval has been confirmed
  ON but its actual blocking behavior (does it pause mid-flight, is there a
  timeout, can it silently no-op) has not yet been observed firsthand.
  Recommends, without requiring, watching the first several automated
  orders closely.
- New **Decision log** entry (Experiment Timeline), same format as the
  2026-10-27 entry, so a future reader sees this was a named decision with
  a stated tradeoff, not a silent rule change.

### Code changes
- None. This is a procedural/documentation change only —
  `robinhood_trade.py`'s guardrail logic, sizing, and `confirmation_prompt()`
  itself are untouched; only how step 8 of the *procedure* uses that
  function's output changed (logged vs. waited-on).

### Comparability
- Does this invalidate the prior performance record? **No.** Nothing about
  scoring, sizing, or the paper book changed. This is a real-money
  execution-authorization change only, and Phase 2B's record stands exactly
  as it did before this entry.


## [v2.4.1] — 2026-10-28

**PATCH** — `get_bars()` has returned stale price data since the very first
commit (2026-07-01). It requested `sort="asc"` over a ~120-calendar-day
window (`start = today - limit*2 days`, `limit=60`, no `end`, no pagination
handling). A 120-day window holds roughly 82 trading days — far more than
60 — and Alpaca's bars endpoint returns the *first* `limit` results in sort
order when more exist, not the most recent `limit`. So every call silently
received the **oldest** 60 bars in the window, not the newest: `closes[-1]`
("last_close") was roughly 4-5 weeks stale, for every symbol, every day,
for the life of this project.

**Found while QA-ing the first live Robinhood attempt.** Alex's terminal
session refused to build real-money candidates because today's Judge
approvals (NOK, COP, IBM, PFE, KDP) disagreed with live Robinhood quotes by
5-17%, in mixed directions — exactly the signature of a stale window (each
stock's own drift over its own missing weeks), not a market-wide outage or
a Robinhood data problem. Independently verified against Yahoo Finance,
Google Finance, Morningstar, CNN, Bloomberg, and MarketChameleon: IBM
closed 10/27 at $250.25 and COP at $147.96 — both match Robinhood exactly;
research.json's $232.65 and $128.10 do not. Robinhood's data was correct.

### Strategy / CLAUDE.md changes
- None directly. See the open question below — this may still need one.

### Code changes
- `research.py`: `get_bars()` now requests `sort="desc"` (truncates from the
  *recent* end instead of the old one) and reverses the result to ascending
  order before returning, so `compute_technicals()` and
  `compute_moving_averages()` — both of which index from the end expecting
  `closes[-1]` to be the latest close — are unaffected by the change in
  fetch direction.
- `tests/test_guardrails.py`: `TestGetBarsReturnsLatestNotOldest` (3 tests)
  — asserts `closes[-1]` is the true latest bar under a synthetic 90-bar
  window (more than `limit`, reproducing the exact over-window condition),
  asserts the request actually uses `sort="desc"` (direct mutation guard),
  and an empty-response sanity check. Mutation-tested against the original
  code: 2 of 3 tests failed when reverted to `sort="asc"` with no reversal,
  confirming the regression is caught.

### Comparability — open question, not resolved by this patch
**Does this invalidate the prior performance record? Unclear — this is a
governance question, not a code question, and is being raised to Alex
rather than decided here.** Every technical signal (MA20/MA50 crossover,
RSI, MACD, Bollinger %, Hard Rule 2's pullback/breakout read) the Judge has
ever scored was computed on a rolling window roughly a month stale. The
mitigating factor: `trade.py`'s actual paper-book buy path always
reconciles the entry limit against a **live** ask price
(`min(ask, research_limit_price)`), so fills were anchored to real
tradable prices, not the stale ones — this is not the same shape of
corruption as the Judge-weight bug that voided Phase 2. But the Judge's
*entry-timing judgment* (was this actually a pullback, was RSI actually
below 45, was the trend actually intact) has been evaluated against
month-old levels throughout Phase 2B, which is a real data-quality question
for whether that record means what it was assumed to mean. This patch
fixes the code; it does not, on its own, decide whether Phase 2B needs
re-baselining the way Phase 2 did on 2026-08-20. That decision is Alex's,
the same way the Phase 2 void was.


## [v2.4.0] — 2026-10-28

**MINOR** — Add `robinhood_trade.py`'s journal entry writer, completing step
9 of the Robinhood Execution Procedure, plus a full synthetic end-to-end dry
run. Alex asked for the journal writer and "a thorough QA of everything
built so far." The dry run is the strongest QA possible without a live
Robinhood session: it proves the whole pipeline — state, stop/phantom
checks, candidate building, confirmation prompt, recording a fill,
journaling, persisting state — chains together correctly, using synthetic
data only. It is explicitly not, and cannot be, a substitute for an actual
live test with a real account connection, which this session does not have
(no Robinhood MCP tools are available here, and the standing prerequisite —
confirming Trade Approval is genuinely on — remains unverified).

### Strategy / CLAUDE.md changes
- None directly; the Robinhood Execution Procedure already documented step
  9 referencing these functions by name before they existed.

### Code changes
- `robinhood_trade.py`: `render_journal_entry()` builds the day's markdown —
  same section order as `journal-template.md`, but deliberately does **not**
  duplicate the Market Research section. Research and Judge scoring are
  account-agnostic and already live in `logs/paper/{date}.md` in the public
  repo for the same day; re-rendering technicals/news here would be a second
  copy of the same data free to drift from the first, exactly the "Doc/code
  drift is a real bug, not a typo" lesson. The real journal instead points
  at the shared one by path and covers only this book's own account state,
  fills, stop-losses, phantom flags, skipped candidates, and reflection.
  `write_journal_file()` persists it to `{logs_dir}/{date}.md`, `logs_dir`
  required with no default, same as every other function in this module.
- `tests/test_robinhood_trade.py`: 6 new tests for the journal writer
  (empty day, fills in the trades table, stop-loss/phantom/skipped sections,
  the no-duplicate-research-section invariant, `logs_dir` required).
- `tests/test_robinhood_dry_run.py` (new): one full synthetic session —
  fresh state, a position that's breached its stop, a Judge APPROVE that
  should buy, a second APPROVE that should be cooldown-skipped, a simulated
  confirmed fill, journaling, and a final reload proving state/trade-log/
  journal all landed correctly and nowhere near the public repo. No network
  call, no MCP tool, no real account anywhere in the file.

### Comparability
- Does this invalidate the prior performance record? **No.** New tooling
  for a book that has placed zero trades; no scoring, sizing, or execution
  logic changed in the paper/options books.


## [v2.3.1] — 2026-10-28

**PATCH** — `intelligence.py` and `screener.py` were missed by the
2026-08-31 naive-clock fix (`naive_clock_mislabelled_as_ET`, Lessons
Learned): both still named their output with raw `date.today()` — the
GitHub Actions runner's UTC clock — instead of `market_time.et_today_iso()`,
while `research.py` looks up `{et_today_iso()}-intelligence.json` /
`{et_today_iso()}-screener.json`. Found during a QA pass requested by Alex
("thorough QA of everything built so far... other parts of agent are
working"), by noticing `logs/paper/2026-10-28-intelligence.json` and
`-screener.json` existed with no corresponding `2026-10-28.json` research
output — the pipeline had run at 9 PM ET on 2026-10-27, still the 27th in
ET but already the 28th in UTC.

**Confirmed as live, ongoing data loss, not a one-off:** any run landing
between UTC midnight and ET midnight — roughly 8 PM–11:59 PM ET, every
single evening — has written that evening's intelligence/screener output
one calendar day ahead of the name `research.py` would ever look under.
`research.py` then silently fell back to whichever same-day-morning file
it last found correctly named, discarding the evening's fresh VIX/fear-greed
reading, insider-buy scan, earnings watch, market discovery, and S&P 500
screen for roughly 4 hours a night — every night since the hourly cadence
began on 2026-10-13.

### Strategy / CLAUDE.md changes
- None. This restores the already-documented intent of the 2026-08-31 fix
  ("used everywhere 'today' determines a filename or cross-script lookup")
  rather than changing it.

### Code changes
- `intelligence.py`: added `from market_time import et_today_iso`.
  Extracted `intelligence_output_path()` and `reddit_report_path()` so the
  path-construction logic is unit-testable without exercising
  `morning_intelligence()`'s full pipeline (VIX, FRED macro, Alpaca
  discovery, Reddit scan, SEC EDGAR). Both now resolve via `et_today_iso()`;
  `write_reddit_report()` and `morning_intelligence()` call the helpers
  instead of inlining `date.today()`.
- `screener.py`: same pattern — `screener_output_path()` extracted and used
  by `full_scan()`, resolving via `et_today_iso()`. Left the S&P 500
  universe cache's staleness check (`date.today()` on both the read at line
  ~83 and the write at line ~114) untouched on purpose: it's a
  self-consistent pair measuring elapsed calendar days for a cache
  freshness check, not a cross-script filename lookup — exactly the
  exception the original 2026-08-31 fix already carved out, and touching
  only one side would raise `TypeError: can't subtract offset-naive and
  offset-aware datetimes` for no benefit. Same reasoning applied to the news
  lookback windows in both files (`cutoff = date.today() - timedelta(...)`)
  — left as pure elapsed-day arithmetic, not identity.
- `tests/test_guardrails.py`: `test_pipeline_modules_import_the_shared_helper_not_bare_datetime`
  now also covers `intelligence` and `screener` — it would have caught this
  immediately had it covered them from the start. Added
  `test_intelligence_and_screener_filenames_agree_with_research_lookup`,
  which calls the new path-builder functions directly under the same
  UTC-crosses-midnight fixture as the original 2026-08-31 incident test.
  Mutation-tested: an initial version of this test only checked that
  `et_today_iso` was importable and did **not** fail when the actual write
  call sites were reverted to `date.today()` — rewritten to call
  `intelligence_output_path()`/`reddit_report_path()`/`screener_output_path()`
  directly, confirmed both mutations now fail it.

### Comparability
- Does this invalidate the prior performance record? **No.** This restores
  intended freshness of intelligence/screener data during a ~4-hour evening
  window; it does not change any scoring, sizing, or Hard Rule. Evening
  trading decisions during the affected window used somewhat staler
  discovery/sentiment context than intended, which is a data-quality
  degradation, not a different ruleset — the Judge still scored against
  whatever context it was actually given.


## [v2.3.0] — 2026-10-28

**MINOR** — Add `robinhood_trade.py`: guardrails, sizing, and journaling for
the real-money book. Alex: "Start on that next" (the interactive executor
deferred in v2.1.0/v2.2.0). This is new tooling only — it places no orders
itself, and has not been run end-to-end against a live account. The
standing prerequisite from the 2026-10-27 setup conversation (confirming
Trade Approval is genuinely ON in Robinhood's Agent Settings) remains
unverified.

### Strategy / CLAUDE.md changes
- New **"Robinhood Execution Procedure"** section, a numbered 10-step
  sequence for the only way this book may trade: an interactive session
  with Robinhood MCP tools, confirming Trade Approval is on, fetching
  real account/position data, running stop/phantom checks before any new
  buy (same order as `trade.py`'s session), building sized candidates,
  and showing the exact Mandatory Confirmation Protocol line before
  placing anything. Step 1 explicitly says not to take a prior session's
  word for Trade Approval being on.
- Robinhood environment subsection and Tools Available both reference the
  new module and procedure.

### Code changes
- `robinhood_trade.py` (new): pure logic only — no Robinhood API/MCP calls,
  same pattern `robinhood_screener.py` already established in this repo
  (functions take already-fetched data as plain dicts). Reuses `trade.py`'s
  account-agnostic Hard Rule functions verbatim: `tier_for`, `stop_pct_for`,
  `position_pct_for`, `regime_adjusted_position_pct`, `is_buyable`,
  `allocation_cap_for`, `check_stop_loss`, `check_inverse_etf_max_hold`,
  `detect_phantom_equity`, `cooldown_remaining_hours`.
- **Deliberately not reused**: `trade.honest_portfolio_value()` and
  `trade.calculate_max_shares()` both internally call
  `trade.detect_phantom_equity(positions)` with no state argument, which
  falls back to `trade.load_state()` — the *paper* book's
  `logs/paper/agent-state.json`. Calling either here would have made a
  real-money sizing decision quietly consult Alpaca's state file.
  `honest_equity()` and `max_shares_for()` run the identical formula but
  always take this book's own state explicitly. Caught by writing
  `TestStateIsolationFromPaperBook`, which asserts `trade.load_state` is
  never called, before writing the implementation.
- `logs_dir` has no default anywhere in this module — `load_real_state`,
  `save_real_state`, and `append_fill` all raise `RealLogsDirNotSet` on an
  empty/missing value rather than falling back to a path inside this repo.
  Mutation-tested: making `_require_logs_dir` a no-op fails 3 tests.
- `confirmation_prompt()` builds the Mandatory Confirmation Protocol's exact
  line in one place and refuses to render a `SKIP` candidate.
- `build_buy_candidates()`: no averaging down (a symbol already held is
  skipped, not resized), 48h cooldown (Hard Rule 4), `$5` price floor,
  watchlist `signal_only`/`max_allocation_pct` respected, RISK_OFF requires
  STRONG_BUY only (Dalio check) and the risk-off ceiling composes correctly
  with VIX sizing via the reused `regime_adjusted_position_pct`.
- `tests/test_robinhood_trade.py` (new, 18 tests). Mutation-tested: removing
  the cooldown check fails `test_cooldown_blocks_reentry`; neutering
  `_require_logs_dir` fails 3 logs-dir tests.

### Comparability
- Does this invalidate the prior performance record? **No.** No scoring,
  sizing, or execution logic in the paper/options books changed. This adds
  a new, currently-unused code path for a book that has placed zero trades.


## [v2.2.0] — 2026-10-27

**MINOR** — Real-money journal and trade-log destination is now a separate
private repo, [`ai-trading-agent-real-logs`](https://github.com/AlexTSung/ai-trading-agent-real-logs)
(created by Alex 2026-10-27), replacing the gitignored local `logs/real/`
scaffolding from v2.0.0/v2.1.0. That local scaffolding was never going to
work as this repo's actual memory model: `logs/real/` has been in
`.gitignore` since early in the project ("personal trading records"), which
means nothing written there would ever be committed, visible to a future
session, or usable by `self_review.py`-style tooling — exactly the three
things Alex asked for ("a log and repository to learn from... to go back
and see any details").

### Strategy / CLAUDE.md changes
- Environment section's Robinhood subsection rewritten: journal and
  trade-log location, why a separate repo is structurally necessary (not
  just a style choice), the file-format parity with `logs/paper/` that lets
  existing tooling read it with only a log-directory override, and that no
  GitHub Actions secret is needed since nothing automated writes there.
- Account Guardrails "Three Books" table's Journal folder row and Hard Rule
  5 updated to point at the private repo instead of `logs/real/`.

### Code changes
- None in this repo. The private repo received its own initial commit:
  `README.md` (purpose, privacy requirement, structure, access pattern) and
  `logs/trade-log.csv` (header only, identical columns to
  `logs/paper/trade-log.csv`) and `logs/journal-template.md` (copy of this
  repo's `journal-template.md`). No executor writes to it yet — that's
  still the same follow-up work flagged in v2.0.0/v2.1.0, now with a real
  destination ready for it.

### Comparability
- Does this invalidate the prior performance record? **No.** Pure
  infrastructure/documentation; no scoring, sizing, or execution logic
  changed in this repo.


## [v2.1.0] — 2026-10-27

**MINOR** — Add `self_review.py`: a recursive self-improvement proposal log
that turns `learning_agent.py` patterns with no automatic handler into dated,
approvable proposals, plus `logs/real/` scaffolding for the eventual
Robinhood journal. Requested by Alex: "the agent to review and start having
a recursive self improvement and I approve each suggestion from the agent
before creating a new version."

### Strategy / CLAUDE.md changes
- New section **"Recursive Self-Improvement — Agent Proposes, Alex
  Approves"**, placed right after the existing Learned-hints rails. Explains
  the gap it closes: `learning_agent.py`'s three auto-applied weight-hint
  actions stay exactly as-is, but two of its detected patterns
  (`tighten_entry_checklist`, `tighten_tier3_stop`) imply an actual Hard Rule
  change that `learning_agent.py` is explicitly forbidden from making, and
  previously just became inert prose in Lessons Learned with no path
  forward.
- Explicit, repeated statement that approving a proposal authorizes the
  *idea*, not the change — implementation still goes through the full
  existing discipline (edit, test, `version.py bump`, changelog entry,
  commit) as a separate step. This is the same "no auto-graduation ever"
  principle applied to the agent's own code, not just to trading real money.
- `Tools Available` lists `learning_agent.py` and `self_review.py`.

### Code changes
- `self_review.py` (new): `propose()` reuses
  `learning_agent.load_calibration()` / `load_judge_lines()` /
  `detect_patterns()` — no duplicated detection logic. Skips any action
  already in `learning_agent.AUTO_HANDLED_ACTIONS`-equivalent set (reduce
  news weight, tighten risk-off gate, raise approve threshold — these stay
  on their existing safe path) and any purely informational action
  (`graduation_check`). For everything else, writes/refreshes a PENDING
  entry in `logs/proposals/proposals.json`: id, evidence, a concrete
  proposed change, and a suggested MAJOR/MINOR/PATCH level. `decide()`
  flips status to APPROVED/REJECTED with a note and records
  `count_at_decision`, so a rejected pattern only resurfaces as a *new*
  dated entry if its count actually grew past that point — recurrence at
  the same count isn't new information and doesn't reopen it. Neither
  `propose()` nor `decide()` writes to any file but
  `logs/proposals/proposals.json`.
- `logs/real/trade-log.csv` created with header only (same columns as
  `logs/paper/trade-log.csv`) and `logs/real/README.md` explaining why it's
  still empty (interactive-only execution, per the v2.0.0 entry).
- `.github/workflows/weekly-self-review.yml` (new): Monday-morning cadence,
  runs `self_review.py propose`, commits `logs/proposals/proposals.json`.
  No broker secrets required — every input is a local file already written
  by the existing paper pipeline.
- `tests/test_self_review.py` (new, 8 tests): covers unhandled-action →
  PENDING proposal, auto-handled/informational actions never proposed,
  re-propose refreshes rather than duplicates, approve/reject set status
  without touching any other file, and the reject→recurrence rule (same
  count stays closed, higher count reopens as a new dated entry
  referencing the prior decision). Mutation-tested: removing the
  auto-handled/informational skip makes `test_auto_handled_action_is_not_proposed`
  and `test_informational_action_is_not_proposed` fail, confirming the gate
  is load-bearing.

### Comparability
- Does this invalidate the prior performance record? **No.** Nothing here
  changes scoring, sizing, or execution — it only adds a reporting/proposal
  layer that writes to a brand-new file. No existing test's expected
  behavior changed.


## [v2.0.0] — 2026-10-27

**MAJOR** — Alex explicitly overrode the Phase 2B graduation gate and authorized
real-money Robinhood trading despite measured criteria not being met. The
2026-10-27 `graduation-decision.yml` run (baseline 2026-08-20) scored:

| Criterion | Required | Actual |
|---|---|---|
| Occurrences (scored buys) | ≥15 | 8 |
| Closed round trips | ≥10 | 6 |
| Net return | ≥+3.0% | unavailable (broker/benchmark fetch failed — fails closed per rule) |
| Profit factor (realized) | ≥1.5 | 11.57 |
| Expectancy/round trip | >0.0% | +6.24% |
| Realized win rate | ≥55% | 83.3% |
| Reviewer win rate | ≥55% | 50.0% |
| Avg Judge score | ≥7.0 | 8.63 |
| Avg Reviewer score | ≥6.0 | 8.31 |
| Beats SPY | required | unavailable |

Verdict was **DO NOT GRADUATE** — 5 of 10 criteria failing, binding constraint
is sample size (8 scored buys / 6 round trips, both under half the required
floor). Alex was told this directly and chose to override anyway: "Override
the gate now, go live anyway." Phase 3 unlocks by that explicit instruction,
not by the agent having earned it under its own documented bar.

### Strategy / CLAUDE.md changes
- Experiment Timeline Phase 3 row changed from `Locked` to
  `Unlocked — manual override 2026-10-27`, with the criteria table above
  reproduced in the Decision log so a future reader sees the override was
  made against the record, not because of it.
- Graduation Rules section gets an explicit note: this override does not
  retroactively mean the criteria were met, and does not change what future
  graduation decisions require — the next time this gate matters (e.g. sizing
  conviction, or a future "should we have gone live" retrospective), the
  actual measured numbers above are what happened, not a passing verdict.
- No Hard Rule, stop-loss tier, position-sizing formula, or Judge scoring
  logic changed. Every existing guardrail (stop-loss tiers, 48h cooldown,
  cash floor, VIX ladder, phantom-equity detection, earnings blackout, the
  Mandatory Confirmation Protocol) applies identically to the real-money book.
- **Execution architecture constraint, newly documented**: the Mandatory
  Confirmation Protocol requires a human to confirm every real-money order
  "in chat," with "no exceptions." That is fundamentally incompatible with
  the unattended, hourly GitHub Actions automation the paper book runs on —
  there is no human present in a scheduled workflow run to confirm anything.
  Real-money execution therefore cannot be wired into `trade.py` /
  `morning-pipeline.yml` the way paper is. Research and Judge scoring stay
  automated (they're account-agnostic); the real-money executor is a
  human-in-the-loop interactive step only, run from a session where Alex is
  present to type the confirmation — never a cron trigger, never
  `workflow_dispatch` on a schedule.

### Code changes
- None yet. `robinhood_screener.py` (read-only IV/greeks) is the only
  existing Robinhood-touching code; there is no real-money executor, no
  `logs/real/` directory, and no `reviewer.py`/`journal.py` support for a
  third book yet. Those are tracked as follow-up work, not part of this
  version bump — this bump documents the policy/gate change only.

### Comparability
- Does this invalidate the prior performance record? **No.** The Phase 2B
  paper baseline (reset 2026-08-20) and every trade counted toward it are
  unchanged and remain the valid, honest measurement of the ruleset's
  performance. This bump does not reset that baseline — it records a
  separate, explicit decision to begin real-money trading *despite* that
  record not yet clearing the bar. A future reader comparing real-money
  results against the Phase 2B numbers should know the gate was overridden,
  not passed, on this date.


## [v1.5.1] — 2026-10-23

**PATCH** — run_trading_session's buy gate was 'sym not in open_syms' (filled positions only), so an unfilled resting limit order was not recognized as already pending -- a symbol that stayed Judge-APPROVE across consecutive hourly runs while its entry bid sat unfilled picked up a brand-new duplicate buy order every run. Found live on the paper account 2026-10-23: PFE had 4 separate resting buy orders and COP 3, accumulated in a single morning now that the pipeline fires reliably every hour (v1.4.1). If more than one of a symbol's stacked limits ever fills, the resulting position silently exceeds every conviction-sizing cap in Hard Rule 6. Fixed by also checking open orders for an existing agent-placed buy order on the same symbol before placing another.

### Why
Found while running the v1.5.0 watchlist additions through the normal
pipeline at the user's request. Two near-simultaneous triggers (a manual
dispatch landing ~45 seconds after the natural hourly cron) both reached the
buy loop, and inspecting the resulting `resting_entries` in that run's
session output showed PFE with 4 distinct order IDs and COP with 3 — all
placed the same morning. The root cause predates the near-simultaneous
trigger, though: `revalidate_resting_entries` ("0b" in `run_trading_session`)
already re-checks every resting GTC entry bid every run specifically because
entries now rest *across* sessions (a deliberate v1.x design choice so a
patient limit at MA20 support can wait for a pullback instead of expiring
nightly) — but nothing before step "2. Execute only Judge-approved buys"
cross-referenced that same resting-order list against new buy candidates.
Any single hourly re-run where a symbol was still Judge-APPROVE and its
earlier bid hadn't filled would place another. This was effectively dormant
before v1.4.1 (one-shot-per-day research meant a symbol rarely got evaluated
twice in a day before its order either filled or went stale) and became live
the moment hourly re-evaluation started working as designed.

### Strategy / CLAUDE.md changes
- None. Hard Rule 6's conviction-scaled sizing was already correct per
  order; this closes the path by which multiple orders for the same symbol
  could each be sized as if it were the only one.

### Code changes
- `trade.py` `run_trading_session()`: added `open_buy_order_syms`, built from
  the same `open_orders` fetch already used for resting-entry revalidation,
  matching on `side == "buy"` and the existing `agent-buy-` client-order-id
  prefix convention (same prefix `revalidate_resting_entries` already
  filters on). All three buy-gating sites (`wants_to_buy`, the dip-buy branch,
  the signal-buy branch) now also require `sym not in open_buy_order_syms`.
  A symbol skipped for this reason gets its own explicit skip reason instead
  of falling into the generic "HOLD or no qualifying signal" bucket.
- Consolidated two inline recomputations of `logs/paper`'s path
  (`load_judge_output`, `run_trading_session`) to use the existing
  module-level `LOGS_DIR` constant instead of each re-deriving it from
  `os.path.dirname(__file__)` — no behavior change, but it's what made this
  change's own test possible to write without touching real log files.
- `tests/test_guardrails.py`: added `TestNoDuplicateRestingBuyOrders` (4
  tests, mutation-tested to confirm each fails if the new check is removed) —
  a fresh buy still places an order with no existing one; one resting
  agent-placed buy order blocks a second; a non-agent order on the same
  symbol does not false-positive as a duplicate; a resting *sell* order
  (e.g. a take-profit) does not block a new buy.

### Comparability
No. This is a correctness fix restoring the sizing guarantee Hard Rule 6
already assumed (one order, one size, per entry) — it doesn't change what
the Judge approves or how a single order is sized.


## [v1.5.0] — 2026-10-23

**MINOR** — Added 8 new watchlist symbols (IBM, PFE, KDP, VZ, LRCX, ADBE, CMCSA, CSX) at the user's direction, from a value-investing review of the 2026-10-22 broad market scan. MSFT and COP from the same review were already on the watchlist. This adds candidates for the normal research/judge/trade pipeline to evaluate -- it does not pre-approve any of them or change any Judge criterion, gate, or sizing rule.

### Why
The user asked for a broad value-investing scan during the current pullback
(enabled by v1.4.4's screener universe fix), then asked for the resulting
top-10 list to be "run through the normal path" so the existing
research/judge/trade pipeline — not an ad hoc manual trade — decides what
actually gets bought. Two of the ten (MSFT, COP) were already watchlist
members; the other eight were only reachable via screener.py's volume-based
discovery, which is not guaranteed to resurface the same names two days
running. Adding them to `watchlist.json` is the supported way to guarantee
`research.py full` evaluates a specific symbol every run, same mechanism
the 2026-08-26 and 2026-06-29 expansions used.

Four of the eight (IBM, PFE, KDP, VZ) were already Judge-APPROVE-tier
pullback entries as of 2026-10-22 — adding them is unlikely to change much on
its own. The other four (LRCX, ADBE, CMCSA, CSX) were in a confirmed
STRONG_SELL downtrend that day, which the Judge's trend-following technical
criterion scores at 0 regardless of quality — these are added specifically so
the pipeline's verdict on them is visible and attributable, not to pre-load an
approval the system's own logic wouldn't currently give.

### Strategy / CLAUDE.md changes
- `watchlist.json`: 8 new entries (symbol, description, max_allocation_pct 5%
  each, notes dated and attributed to this review). New `telecom` correlation
  group tagging VZ and CMCSA, since both are legitimate media/telecom
  correlation risk per the Correlation Check (Bridgewater) section.
- Noted but not changed: none of the 8 new symbols are in `trade.py`'s
  hardcoded `TIER1_SYMBOLS`/`TIER2_SYMBOLS` sets, so all default to the
  TIER3 -10% stop regardless of actual market cap/quality (IBM and PFE are
  both $170B+ mega-caps). Also noted: `judge.py`'s hardcoded `tech_names`
  correlation set does not include LRCX, so it will not trigger the existing
  tech-correlation penalty against the open NVDA position despite both being
  semiconductor-sector bets. Both are pre-existing gaps in those hardcoded
  sets, not something this change fixes — flagged in the watchlist notes so
  a human reviewing position sizing sees the gap even though the code
  doesn't account for it.

### Code changes
- None.

### Comparability
No. Adding watchlist candidates is explicitly listed as a MINOR, safe-with-
the-record change in this file's own versioning table — it widens what the
Judge gets asked to look at, it doesn't change how the Judge, executor, or
any Hard Rule decides once asked.


## [v1.4.4] — 2026-10-22

**PATCH** — screener.py's S&P 500 universe fetch regex-scraped Wikipedia's HTML table and silently returned [] on every call for at least two months (universe-cache.json dated 2026-08-21 held exactly the 37-symbol NDX_EXTRAS fallback, in that exact order -- the only way that happens is if get_sp500_symbols() returned [] every single refresh). The documented 'S&P 500 + Nasdaq 100 full market screener' had actually been scanning 37 mega-cap tech names the whole time. Fixed by fetching a maintained structured CSV dataset instead of scraping Wikipedia's HTML, and by refusing to cache a degraded (<100 symbol) universe for 7 days so a future fetch failure fails loud and short-lived instead of silent and week-long.

### Why
Found while sizing up a user request for a broad (1000+ symbol) value-investing
scan during the current sell-off. Checking what the live screener actually
covers, `logs/paper/universe-cache.json` held exactly 37 symbols, in exactly
the order of the hardcoded `NDX_EXTRAS` fallback list in `get_cached_universe()`
-- not a plausible real S&P 500 sample, a precise match. That only happens if
`get_sp500_symbols()` had returned `[]` every time the cache refreshed. The
dev branch's own copy of this file is dated 2026-08-21, so this had been true
for at least two months: CLAUDE.md's "full market screener (S&P 500 + Nasdaq
100 + news firehose)" was actually running against 37 mega-cap tech names.
Nothing errored, because a 37-symbol universe is a completely valid input to
every downstream step -- it just silently finds far fewer candidates than
documented, exactly the "a broad except Exception: return [] is where a real
bug goes to hide" pattern already in this file's Lessons Learned.

`en.wikipedia.org` was unreachable from this session's own sandbox, so the
exact HTML change that broke the regex couldn't be directly inspected; it
doesn't matter why a regex tied to one page's current markup broke, only
that it did, silently, and that regex-scraping an unversioned HTML page is
the wrong approach regardless. Replaced with a fetch against
`datasets/s-and-p-500-companies`'s maintained `constituents.csv` on GitHub --
confirmed reachable and correctly structured (`Symbol` as the first column)
before writing the fix, parsed with the standard library `csv` module instead
of a hand-rolled pattern.

### Strategy / CLAUDE.md changes
- None. This restores the documented screening universe; no Judge criterion,
  Hard Rule, or sizing rule changed.

### Code changes
- `screener.py`: `get_sp500_symbols()` now fetches
  `raw.githubusercontent.com/datasets/s-and-p-500-companies/main/data/constituents.csv`
  and parses it with `csv.DictReader`, instead of a regex over Wikipedia's
  HTML. Symbols with a `.` (e.g. `BRK.B`) are still normalized to `-` to match
  Alpaca's symbology.
- `get_cached_universe()`: a fetch that returns fewer than 100 symbols is no
  longer written to the 7-day cache -- it's used for that one run (falling
  back to the 37-symbol `NDX_EXTRAS` list, same as before) and logged loudly,
  but the next run retries the real fetch instead of inheriting a silently
  degraded universe for a week.
- Removed `NDX100_URL`, a module-level constant that was assigned and never
  read anywhere in the file.
- `tests/test_guardrails.py`: added `TestSP500UniverseFetch` (4 tests) —
  CSV parsing including the `BRK.B` → `BRK-B` normalization, request-failure
  handling, and that a degraded universe is excluded from the cache while a
  healthy one is cached.
- Deleted the stale `logs/paper/universe-cache.json` (dated 2026-08-21) so
  the next real pipeline run regenerates it with the fixed fetch rather than
  reusing the 37-symbol result for up to 7 more days.

### Comparability
No. This restores the documented screening universe — it doesn't change any
Judge criterion, gate, or sizing rule. More candidates reaching the Judge is
a wider funnel, not a different one.


## [v1.4.3] — 2026-10-22

**PATCH** — journal.py's displayed 'Cash Reserve' percentage divided cash by Alpaca's raw reported portfolio_value (phantom-inflated by LBGJ), not honest equity -- the same denominator bug Hard Rule 11 exists to prevent, except here it only affected the human-facing daily report, not actual Judge/trade decisions (judge.py and trade.py already divide by honest_portfolio_value correctly). On 2026-10-21 this understated the true cash reserve as 9.6% when the Judge's own honest-equity math shows 34.7% -- a 3.6x discrepancy that made the book look cash-starved when it was not. Fixed by adding the same honest_portfolio_value helper (matching judge.py's and trade.py's existing copies) and computing Cash Reserve against it; the journal now also shows both the reported and honest portfolio value side by side so the two numbers are never silently conflated again.

### Why
Found while sizing up a user request to trim winning positions and lower the
20% cash floor to free up more capital for trading. The premise driving that
request — cash reserve reading 7-10% in recent journals — turned out to be
measuring the wrong thing. `journal.py`'s "Cash Reserve" line used
`cash / account["portfolio_value"]`, Alpaca's raw figure, which still
includes LBGJ's ~$250K of phantom equity from the unreconciled reverse split.
`judge.py`'s `score_portfolio()` and `trade.py`'s sizing logic were never
affected — both already call their own `honest_portfolio_value()` — so no
trading decision was distorted. But every daily journal since LBGJ went bad
(2026-08-03 onward) has been reporting a cash reserve number roughly 3-4x
lower than what the Judge actually checks, making the book look chronically
cash-starved in the one place a human reads it.

### Strategy / CLAUDE.md changes
- None. `judge.py`'s and `trade.py`'s Hard Rule 8 enforcement were already
  correct; this only fixes what the daily journal displays to a human reader.

### Code changes
- `journal.py`: added `honest_portfolio_value()`, matching the existing
  copies in `judge.py` and `trade.py` (same docstring lineage, same
  `detect_phantom_equity` reuse). The EOD journal's "Portfolio Status"
  section now shows both `Portfolio Value (reported)` and
  `Portfolio Value (honest — phantom equity at cost)`, and "Cash Reserve" is
  computed against the honest figure with a note that this is what Hard
  Rule 8 and the Judge actually gate on.

### Comparability
No. This is a reporting fix — the Judge and executor's actual cash-floor
enforcement is unchanged and was already correct. Only what the journal
displays to a human reader changes.


## [v1.4.2] — 2026-10-20

**PATCH** — GitHub Actions artifact storage quota was exhausted by 30-day retention on hourly log-artifact uploads across 4 equities workflows, which started marking every run 'failure' from ~5 AM ET on 2026-10-20 at the upload-artifact step (the actual pipeline logic and git commit/push in every affected run had already succeeded — this was a cosmetic/monitoring-reliability bug, not a trading-correctness one). Fixed by dropping retention-days to 1 on all 4 equities workflows' log-artifact uploads, since the same content is already committed to git by the preceding step and doesn't need a second, redundant 30-day copy; existing backlog of ~1388 old artifacts cleared via the Actions API to recover quota immediately rather than waiting weeks for them to individually expire.

### Why
Found while investigating a user-reported check-in: `options-pipeline.yml` and
`morning-pipeline.yml` runs both showed red "failure" starting at the
2026-10-20T09:00 UTC (5:00 AM ET) trigger and on every run since. Job logs
showed the actual cause was the *last* step, `actions/upload-artifact@v5`:
`##[error]Failed to CreateArtifact: Artifact storage quota has been hit.`
Every step before it — installing deps, running the pipeline script, `git
commit`/`git push` of `logs/paper/` or `logs/options/` — had already
succeeded. So no research, judge, trade, or options decision was lost; the
quota hit only broke the redundant post-hoc log backup and, as a side
effect, painted every run red in the Actions UI going forward.

Root cause: hourly `workflow_dispatch` triggers (per the external cron
cadence documented in CLAUDE.md's Daily Schedule) across 4 equities
workflows, each uploading a fresh `retention-days: 30` artifact of its whole
log directory on every run, accumulated 1388 artifacts (~2.8 GB) over about
a week — enough to exceed this private repo's Actions storage quota.

### Strategy / CLAUDE.md changes
- None. This is a CI/monitoring fix, not a change to any trading criterion,
  Hard Rule, or Judge/executor behavior.

### Code changes
- `morning-pipeline.yml`, `midday-pipeline.yml`, `eod-pipeline.yml`,
  `options-pipeline.yml`: `retention-days` on the log-artifact upload step
  dropped from 30 to 1. The artifact is a convenience copy for same-day
  debugging; `logs/paper/` and `logs/options/` are already committed to git
  by the step immediately before it, so nothing is lost by not also keeping
  a month of redundant zip copies.
- Cleared the existing backlog of ~1388 artifacts via
  `DELETE /repos/{owner}/{repo}/actions/artifacts/{id}` so quota recovers
  immediately rather than waiting for each artifact's original 30-day window
  to lapse individually (which would have taken until mid-November for the
  oldest ones).

### Comparability
No. Pure CI/ops change — no Judge criterion, gate, or sizing rule touched.


## [v1.4.1] — 2026-10-20

**PATCH** — run_github.sh GUARDRAIL G1 used the checked-out file's filesystem mtime to measure research staleness, but actions/checkout rewrites that mtime to the run's own start time every job, so AGE_SECONDS always read ~0 and the pipeline silently regressed to one-shot-per-day for a full week (2026-10-14 through 2026-10-20), same bug v1.4.0 was meant to fix. Fixed by reading research.py's own embedded timestamp field instead of stat().

### Why
v1.4.0 replaced G1's one-shot-per-day lock with a 50-minute rolling interval,
measured via `stat -c %Y "$LOG_DIR/$DATE.json"`. That works on a long-lived
local checkout, which is what it was tested against — but every GitHub
Actions job starts with a fresh `actions/checkout`, which writes the file to
disk at checkout time. The file's mtime is therefore always "the moment this
job started," never "the moment research.py actually produced this content."
`AGE_SECONDS` read ~0 on every single run after the first of the day, so
`AGE_SECONDS -lt MIN_RERUN_SECONDS` was true every time and G1 skipped —
functionally identical to the one-shot lock it replaced.

Confirmed directly in job logs: every `morning-pipeline.yml` run from
2026-10-14 through 2026-10-20, at every hour of the day, logged `GUARDRAIL
G1: Research ran 0m ago (< 50m) — skipping.` `logs/paper/$DATE.json`'s own
`timestamp` field shows the only real research run each of those seven days
landed at ~00:01 AM ET — market closed — exactly the failure mode v1.4.0 was
written to fix, now caused by the fix's own measurement method instead of by
the lock it replaced.

**Concrete cost.** The Judge scored real APPROVE-worthy candidates off that
midnight-stale data on three separate days, and none of them ever got a
second look during actual market hours: NVDA/MSFT/NU (2026-10-13, documented
in the v1.4.0 entry above), BAC (2026-10-15), and COP/NFLX/MRVL (2026-10-16).
`trade-log.csv` shows no equities buy between 2026-10-12 (TGT/ARGX/MRK) and
this fix landing. Separately, the book's cash reserve also fell to 7.2% by
2026-10-19 (`logs/paper/2026-10-19.md`), well under the Hard Rule 8 floor —
on days the Judge did evaluate fresh data, that floor would have blocked new
buys on its own regardless of this bug, so not every quiet day traces to G1.
But 10-13/15/16 scored real APPROVEs before the cash floor bound, and those
specifically were lost to this bug.

### Strategy / CLAUDE.md changes
- None. No Judge criterion, Hard Rule, or sizing rule changed.

### Code changes
- `run_github.sh` GUARDRAIL G1: replaced the `stat`-based mtime check with a
  Python one-liner that reads the `timestamp` field already written inside
  `logs/paper/$DATE.json` by `research.py`, and compares it to the current
  US/Eastern time via `zoneinfo`. This value lives in the committed file
  content, not the filesystem, so a fresh `actions/checkout` cannot reset it.
  Falls back to treating the file as maximally stale (forcing a re-run, not a
  skip) if the JSON is missing, malformed, or lacks the field — failing open
  toward an extra research run costs Actions minutes; failing closed toward a
  skip costs a trading day, so the asymmetry is deliberate.

### Comparability
No. This is a correctness fix to v1.4.0's own stated intent (an hourly
rolling re-run interval) — the code was simply wrong, not the rule. No Judge
criterion, gate, or sizing rule changed.


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
