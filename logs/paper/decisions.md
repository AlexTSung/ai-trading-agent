# Decisions Log — Paper Trading Experiment

---

## 2026-06-29 — Extend experiment through July 7

**Decision**: Extend Phase 1 paper trading from June 30 → July 7 (7 additional days).

**Why**: All 6 closed positions ended negative. Realized losses indicate the original ruleset had systematic flaws, not bad luck:
- 8% stop too tight for quality Tier 1 stocks — normal volatility was triggering exits
- Same-day re-entry after a stop locked in back-to-back losses on the same symbol
- Entering at or above breakout levels instead of waiting for MA20 pullbacks
- No minimum hold allowed short-term noise to shake out high-conviction positions

**Ruleset changes applied 2026-06-29**:
- Tiered stops: Tier 1 (AAPL/MSFT/GOOGL/AMZN/NVDA) 20% or thesis-based, Tier 2 (DDOG/TSLA/QCOM) 12%, Tier 3 (NOK/PYPL) 10%
- 48-hour cooling-off after any stop triggers
- Entry targets MA20 pullbacks (`limit = MA20 × 0.995`), not breakouts
- 30-day minimum hold for Judge score ≥ 8.0
- T1/T2 partial profit-taking (sell 40% at T1, 40% at T2)
- Conviction-scaled sizing (9.0+ = 8%, 7.5-8.9 = 5%, 7.0-7.4 = 3%)

**What Phase 2 must prove**:
- Win rate ≥ 55% on closed positions by July 7
- At least 3 profitable exits using the new rules
- No repeated same-symbol stop-and-rebuy within 48 hours

**Next decision point**: July 7, 2026 — review Phase 2 results and decide whether to graduate to Robinhood.

---
