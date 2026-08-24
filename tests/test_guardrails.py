#!/usr/bin/env python3
"""
Regression tests for the trading agent's safety controls.

Every test here corresponds to a failure that ACTUALLY HAPPENED. This file
exists because the same protections were lost or broken three separate times:

  - 2026-07-24  LBGJ: 154,903 shares of a $0.03 stock bought after the Judge
                summed criteria past their own weights to hit exactly 8.5
  - 2026-08-21  NVDA: an authorised stop-loss rejected by the broker because the
                agent's own take-profit orders had reserved the shares
  - fd79d60     trade.py and judge.py overwritten with pre-fix copies, silently
                reverting eight equities safety controls at once

Rails and documentation reduce collisions. Tests are what turn the next one into
a failed check instead of a silent revert.

Run:  python3 -m unittest discover -s tests -v
      python3 tests/test_guardrails.py
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APCA_API_KEY_ID", "test")
os.environ.setdefault("APCA_API_SECRET_KEY", "test")

import judge          # noqa: E402
import reviewer       # noqa: E402
import trade          # noqa: E402


# ══════════════════════════════════════════════════════════════════════════════
# The LBGJ trade — the Judge scoring defect that let it through
# ══════════════════════════════════════════════════════════════════════════════
class TestJudgeScoreClamping(unittest.TestCase):
    """
    score_technical returns up to 3.0 and score_macro/volatility up to 1.5,
    against weights of 2.0/1.0/1.0. Summed raw, that inflated every total by up
    to 2.0 points. LBGJ landed on exactly 8.5 and cleared the 8.5 discovery gate.
    """

    def test_weights_sum_to_ten(self):
        total = (judge.W_TECHNICAL + judge.W_NEWS + judge.W_MACRO
                 + judge.W_PORTFOLIO + judge.W_VOLATILITY + judge.W_QUALITY)
        self.assertEqual(total, judge.MAX_TOTAL_SCORE)

    def test_criterion_cannot_exceed_its_weight(self):
        score, note = judge._clamp((3.0, "STRONG_BUY"), judge.W_TECHNICAL)
        self.assertEqual(score, judge.W_TECHNICAL)
        self.assertIn("capped", note)

    def test_clamp_floors_at_zero(self):
        self.assertEqual(judge._clamp((-5.0, ""), 2.0)[0], 0.0)

    def test_lbgj_scores_below_the_discovery_gate(self):
        """The exact 2026-07-24 breakdown must now total 7.0, not 8.5."""
        raw = {"technical": 3.0, "news": 1.0, "macro": 1.5,
               "portfolio": 2.0, "volatility": 1.0, "quality": 0.0}
        weights = {"technical": judge.W_TECHNICAL, "news": judge.W_NEWS,
                   "macro": judge.W_MACRO, "portfolio": judge.W_PORTFOLIO,
                   "volatility": judge.W_VOLATILITY, "quality": judge.W_QUALITY}
        capped = sum(judge._clamp((v, ""), weights[k])[0] for k, v in raw.items())

        self.assertEqual(sum(raw.values()), 8.5, "historical uncapped total")
        self.assertEqual(capped, 7.0)
        self.assertLess(capped, judge.SCORE_GATE_DISCOVERY,
                        "a discovery stock scoring 7.0 must not clear the 8.5 gate")

    def test_discovery_stock_with_no_quality_evidence_is_vetoed(self):
        self.assertGreater(judge.DISCOVERY_MIN_QUALITY, 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# Entry price safety
# ══════════════════════════════════════════════════════════════════════════════
class TestPriceFloors(unittest.TestCase):

    def test_penny_stock_floor_is_five_dollars(self):
        self.assertEqual(trade.MIN_TRADE_PRICE, 5.00)

    def test_degenerate_exits_are_refused_at_sub_penny_prices(self):
        """
        Rounding a stop to cents collapses the distance to zero on a $0.03 entry,
        making take-profit == stop == entry. LBGJ had no functioning exits at all.
        """
        result = trade.place_oco_exits("LBGJ", 154903, 0.0288)
        self.assertFalse(result[0].get("_ok", False))
        self.assertIn("Degenerate", result[0]["error"])

    def test_normal_priced_stop_distance_is_positive(self):
        stop_pct = trade.stop_pct_for("AAPL")
        entry = 300.0
        stop = round(entry * (1 + stop_pct), 2)
        self.assertGreater(round(entry - stop, 2), 0)

    def test_research_vs_execution_deviation_limit_exists(self):
        """$1.24 researched close against a $0.03 live ask must not be tradeable."""
        self.assertLessEqual(trade.MAX_PRICE_DEVIATION, 0.25)
        deviation = abs(0.03 - 1.24) / 1.24
        self.assertGreater(deviation, trade.MAX_PRICE_DEVIATION)

    def test_zero_ask_is_not_treated_as_a_price(self):
        with mock.patch.object(trade, "requests") as rq:
            rq.get.return_value.json.return_value = {"quote": {"ap": 0}}
            rq.get.return_value.raise_for_status = lambda: None
            self.assertIsNone(trade.get_latest_ask("ILLIQUID"))


# ══════════════════════════════════════════════════════════════════════════════
# Tiered stops and conviction sizing (CLAUDE.md Hard Rules 3 and 6)
# ══════════════════════════════════════════════════════════════════════════════
class TestTieredStops(unittest.TestCase):

    def test_tiers_match_the_documented_percentages(self):
        cases = [("AAPL", "TIER1", -0.20), ("NVDA", "TIER1", -0.20),
                 ("DDOG", "TIER2", -0.12), ("TSLA", "TIER2", -0.12),
                 ("NOK", "TIER3", -0.10), ("SQQQ", "INVERSE", -0.05)]
        for sym, tier, pct in cases:
            with self.subTest(symbol=sym):
                self.assertEqual(trade.tier_for(sym), tier)
                self.assertAlmostEqual(trade.stop_pct_for(sym), pct)

    def test_blue_chip_is_not_stopped_out_at_minus_nine_percent(self):
        """The old flat -8% cut Tier 1 names inside normal volatility."""
        positions = [{"symbol": "AAPL", "qty": "17", "avg_entry_price": "283",
                      "current_price": "257", "unrealized_plpc": "-0.09"}]
        triggered = trade.check_stop_loss(positions, {"stop_outs": {}, "entries": {}})
        self.assertEqual(triggered, [], "AAPL at -9% must hold under a 20% stop")

    def test_speculative_flag_maps_to_tier3(self):
        self.assertEqual(trade.tier_for("AAPL", is_speculative=True), "TIER3")

    def test_conviction_sizing_ladder(self):
        self.assertAlmostEqual(trade.position_pct_for(9.5), 0.08)
        self.assertAlmostEqual(trade.position_pct_for(8.0), 0.05)
        self.assertAlmostEqual(trade.position_pct_for(7.1), 0.03)

    def test_profit_taking_leaves_a_runner(self):
        self.assertLess(trade.T1_SELL_FRACTION * 2, 1.0,
                        "T1 + T2 must leave a remainder to run")


# ══════════════════════════════════════════════════════════════════════════════
# Holding-period rules (Hard Rules 4 and 5)
# ══════════════════════════════════════════════════════════════════════════════
class TestHoldingRules(unittest.TestCase):

    def test_cooldown_blocks_reentry_within_48h(self):
        state = {"stop_outs": {"NOK": (datetime.now() - timedelta(hours=6)).isoformat()},
                 "entries": {}}
        self.assertGreater(trade.cooldown_remaining_hours("NOK", state), 0)

    def test_cooldown_expires(self):
        state = {"stop_outs": {"NOK": (datetime.now() - timedelta(hours=50)).isoformat()},
                 "entries": {}}
        self.assertEqual(trade.cooldown_remaining_hours("NOK", state), 0)

    def test_min_hold_applies_only_to_high_conviction(self):
        recent = (datetime.now() - timedelta(days=5)).isoformat()
        state = {"stop_outs": {}, "entries": {
            "AAPL": {"date": recent, "judge_score": 8.5},
            "CIFR": {"date": recent, "judge_score": 7.2}}}
        self.assertGreater(trade.min_hold_days_remaining("AAPL", state), 0)
        self.assertEqual(trade.min_hold_days_remaining("CIFR", state), 0)

    def test_inverse_etf_max_hold_is_enforced(self):
        state = {"stop_outs": {}, "entries": {
            "SQQQ": {"date": (datetime.now() - timedelta(days=4)).isoformat(),
                     "judge_score": 7.5}}}
        positions = [{"symbol": "SQQQ", "qty": "10", "avg_entry_price": "20",
                      "current_price": "19", "unrealized_plpc": "-0.05"}]
        self.assertTrue(trade.check_inverse_etf_max_hold(positions, state))


# ══════════════════════════════════════════════════════════════════════════════
# Phantom equity (Hard Rule 11)
# ══════════════════════════════════════════════════════════════════════════════
LBGJ_POSITION = {"symbol": "LBGJ", "qty": "154903", "avg_entry_price": "0.0288",
                 "current_price": "3.09", "unrealized_plpc": "106.29"}
REAL_POSITIONS = [
    {"symbol": "AAPL", "qty": "17", "avg_entry_price": "283.70",
     "current_price": "312.00", "unrealized_plpc": "0.0998"},
    {"symbol": "NVDA", "qty": "25", "avg_entry_price": "192.81",
     "current_price": "217.51", "unrealized_plpc": "0.1281"},
]


class TestPhantomEquity(unittest.TestCase):

    def test_unadjusted_split_is_detected_and_blocked(self):
        flags = trade.detect_phantom_equity([LBGJ_POSITION] + REAL_POSITIONS)
        self.assertEqual([f["symbol"] for f in flags], ["LBGJ"])
        self.assertIn("BLOCKED", flags[0]["action"])
        self.assertGreater(flags[0]["phantom_amount"], 400_000)

    def test_genuine_gains_are_not_flagged(self):
        self.assertEqual(trade.detect_phantom_equity(REAL_POSITIONS), [])

    def test_honest_value_restates_phantom_to_cost(self):
        account = {"portfolio_value": "565533.60", "cash": "71258.65",
                   "buying_power": "1286084.24"}
        positions = [LBGJ_POSITION] + REAL_POSITIONS
        honest = trade.honest_portfolio_value(account, positions)
        self.assertLess(honest, 150_000, "phantom equity must be written down")
        cash_pct = 71258.65 / honest
        self.assertGreater(cash_pct, 0.20,
                           "true cash is ~78%; the reported 12.6% froze all buying")

    def test_sizing_uses_honest_value(self):
        """An 8% cap computed off the inflated value was $45,243 on a $91K book."""
        account = {"portfolio_value": "565533.60", "cash": "71258.65",
                   "buying_power": "1286084.24"}
        positions = [LBGJ_POSITION] + REAL_POSITIONS
        qty, _ = trade.calculate_max_shares("MSFT", account, 312.0, 0.08, positions)
        self.assertLess(qty * 312.0, 20_000)


# ══════════════════════════════════════════════════════════════════════════════
# Order plumbing — failures must not be recorded as fills
# ══════════════════════════════════════════════════════════════════════════════
class TestOrderPlumbing(unittest.TestCase):

    def test_rejection_is_not_a_success(self):
        self.assertFalse(trade.order_succeeded(
            {"_ok": False, "error": "Order rejected (HTTP 403)"}))
        self.assertFalse(trade.order_succeeded({}))
        self.assertTrue(trade.order_succeeded({"_ok": True, "id": "abc"}))

    def test_exit_orders_are_released_before_a_risk_sell(self):
        """
        2026-08-21: NVDA's authorised stop was rejected with "insufficient qty
        available (requested 25, available 0)" because the OCO take-profit legs
        had reserved the shares. Cancel must happen before the closing order.
        """
        import inspect
        src = inspect.getsource(trade.close_position)
        self.assertIn("cancel_sell_orders_for", src)
        self.assertLess(src.index("cancel_sell_orders_for"),
                        src.index("place_limit_order"))

    def test_urgent_exits_use_a_wider_limit(self):
        self.assertGreater(trade.URGENT_EXIT_SLIPPAGE, trade.LIMIT_SLIPPAGE)


# ══════════════════════════════════════════════════════════════════════════════
# Entry-quality vetoes
# ══════════════════════════════════════════════════════════════════════════════
class TestChaseVeto(unittest.TestCase):

    def test_overbought_rsi_is_vetoed(self):
        veto, why = judge.check_chase_veto(
            {"rsi": 83, "ma20": 100, "last_close": 101}, "PYPL")
        self.assertTrue(veto)
        self.assertIn("RSI", why)

    def test_extended_above_ma20_is_vetoed(self):
        veto, _ = judge.check_chase_veto(
            {"rsi": 50, "ma20": 100, "last_close": 108}, "X")
        self.assertTrue(veto)

    def test_clean_pullback_is_allowed(self):
        veto, _ = judge.check_chase_veto(
            {"rsi": 42, "ma20": 100, "last_close": 99}, "X")
        self.assertFalse(veto)


class TestEarningsVeto(unittest.TestCase):
    """
    On 2026-08-21 this vetoed 7 of the watchlist on 13F headlines during filing
    season. None concerned the vetoed company's own earnings.
    """

    THIRTEEN_F = [
        ("MSFT", "Chris Hohn's TCI Revamps Q2 Portfolio, Exits Microsoft"),
        ("GOOGL", "TCI Revamps Q2 Portfolio, Adds to Alphabet"),
        ("NOK", "Citadel and Millennium Boosted Fabrinet Stakes in Q2"),
        ("QCOM", "Viking Global Bets Big On SpaceX While Ditching CoreWeave in Q2"),
        ("DDOG", "Consumer Tech: SpaceX Released Strong First Earnings"),
        ("AAPL", "Broadcom Steps up NVIDIA Challenge With $100 Billion Deal"),
    ]

    def test_thirteen_f_headlines_do_not_veto(self):
        for sym, headline in self.THIRTEEN_F:
            with self.subTest(symbol=sym):
                research = {"intelligence": {
                    "earnings_watch_symbols": [sym],
                    "earnings_watch_detail": {sym: headline}}}
                veto, _ = judge.check_earnings_veto([], sym, research)
                self.assertFalse(veto, f"false positive on: {headline}")

    def test_eps_does_not_match_inside_other_words(self):
        """"eps" matched inside "Steps up", vetoing two names on unrelated news."""
        self.assertFalse(judge._is_upcoming_earnings("Broadcom Steps up challenge"))

    def test_genuine_upcoming_earnings_still_vetoes(self):
        for text in ["Nvidia is scheduled to report Q2 earnings next week",
                     "Apple to report quarterly results ahead of the bell",
                     "Tesla EPS preview: analysts expect a beat"]:
            with self.subTest(text=text):
                self.assertTrue(judge._is_upcoming_earnings(text))

    def test_backward_looking_recap_does_not_veto(self):
        self.assertFalse(judge._is_upcoming_earnings(
            "Microsoft reported Q2 earnings last quarter and beat estimates"))


# ══════════════════════════════════════════════════════════════════════════════
# Volatility regime (Citadel rule)
# ══════════════════════════════════════════════════════════════════════════════
class TestVolatilitySizing(unittest.TestCase):

    def test_vix_drives_the_volatility_score(self):
        calm = judge.score_volatility({"intelligence": {"vix": 12.0}})[0]
        hot = judge.score_volatility({"intelligence": {"vix": 38.0}})[0]
        self.assertGreater(calm, hot)
        self.assertEqual(hot, 0.0)

    def test_thresholds_match_the_documented_rule(self):
        self.assertEqual(judge.VIX_HALF_SIZE, 25.0)
        self.assertEqual(judge.VIX_NO_NEW_POSITIONS, 35.0)


# ══════════════════════════════════════════════════════════════════════════════
# Learned hints — the four rails
# ══════════════════════════════════════════════════════════════════════════════
class TestLearnedHintRails(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.hints = os.path.join(self.tmp, "hints.json")
        self.trips = os.path.join(self.tmp, "round-trips.csv")
        self._orig = (judge.HINTS_PATH, judge.ROUND_TRIPS_PATH)
        judge.HINTS_PATH, judge.ROUND_TRIPS_PATH = self.hints, self.trips

    def tearDown(self):
        judge.HINTS_PATH, judge.ROUND_TRIPS_PATH = self._orig

    def _write_trips(self, n):
        with open(self.trips, "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "entry_date", "exit_date", "qty", "entry",
                        "exit", "pnl_dollar", "pnl_pct", "hold_days"])
            for _ in range(n):
                w.writerow(["AAPL", "2026-08-01", "2026-08-10", 1, 100, 101, 1, 1.0, 9])

    def _write_hints(self, **kw):
        with open(self.hints, "w") as f:
            json.dump(kw, f)

    def test_rail1_foreign_scope_is_refused(self):
        self._write_trips(50)
        self._write_hints(scope="oanda_forex", approve_threshold=9.0)
        self.assertEqual(judge.load_learned_hints()["applied"], {})

    def test_rail2_small_sample_holds_everything_back(self):
        self._write_trips(12)
        self._write_hints(scope="alpaca_equities", approve_threshold=8.0,
                          news_weight_adj=0.81)
        self.assertEqual(judge.load_learned_hints()["applied"], {})

    def test_rail3_gate_may_tighten(self):
        self._write_trips(50)
        self._write_hints(scope="alpaca_equities", approve_threshold=8.0)
        self.assertEqual(
            judge.load_learned_hints()["applied"].get("watchlist_gate"), 8.0)

    def test_rail3_gate_may_never_loosen(self):
        """A loop that can lower its own bar eventually will."""
        self._write_trips(50)
        self._write_hints(scope="alpaca_equities", approve_threshold=5.5)
        applied = judge.load_learned_hints()["applied"]
        self.assertNotIn("watchlist_gate", applied)

    def test_rail4_weight_is_clamped_both_directions(self):
        self._write_trips(50)
        for hint, expected in ((0.50, 0.85), (1.40, 1.15), (0.95, 0.95)):
            with self.subTest(hint=hint):
                self._write_hints(scope="alpaca_equities", news_weight_adj=hint)
                self.assertAlmostEqual(
                    judge.load_learned_hints()["applied"]["news_weight_mult"],
                    expected, places=3)

    def test_missing_file_degrades_silently(self):
        self.assertEqual(judge.load_learned_hints()["applied"], {})


# ══════════════════════════════════════════════════════════════════════════════
# Two agents must not share scoring state
# ══════════════════════════════════════════════════════════════════════════════
class TestAgentIsolation(unittest.TestCase):

    ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    def _read(self, name):
        path = os.path.join(self.ROOT, name)
        if not os.path.exists(path):
            self.skipTest(f"{name} not present")
        with open(path) as f:
            return f.read()

    def test_forex_never_reads_alpaca_learned_state(self):
        for name in ("forex_judge.py", "forex_optimizer.py", "forex_trade.py"):
            with self.subTest(file=name):
                src = self._read(name)
                self.assertNotIn("alpaca_judge_hints", src)
                self.assertNotIn("logs/learnings", src)

    def test_alpaca_never_reads_forex_state(self):
        src = self._read("judge.py")
        self.assertNotIn("forex_config", src)
        self.assertNotIn("logs/forex", src)

    def test_the_two_judges_score_different_criteria(self):
        alpaca = self._read("judge.py")
        forex = self._read("forex_judge.py")
        self.assertIn("W_QUALITY", alpaca)
        self.assertNotIn("W_CARRY", alpaca)
        self.assertIn("W_CARRY", forex)
        self.assertNotIn("W_QUALITY", forex)


# ══════════════════════════════════════════════════════════════════════════════
# Round-trip P&L
# ══════════════════════════════════════════════════════════════════════════════
def _order(sym, side, qty, price, day):
    return {"id": f"{sym}-{side}-{day}-{price}", "symbol": sym, "side": side,
            "filled_qty": str(qty), "filled_avg_price": str(price),
            "filled_at": f"{day}T15:00:00Z", "status": "filled"}


class TestRoundTrips(unittest.TestCase):

    def test_fifo_consumes_the_oldest_lot_first(self):
        orders = [_order("QQQ", "buy", 6, 733.16, "2026-06-17"),
                  _order("QQQ", "buy", 6, 708.87, "2026-07-21"),
                  _order("QQQ", "sell", 6, 674.80, "2026-07-28")]
        trips, _ = reviewer.build_round_trips(orders)
        self.assertEqual(len(trips), 1)
        self.assertAlmostEqual(trips[0]["entry"], 733.16)

    def test_partial_sells_split_across_lots(self):
        orders = [_order("X", "buy", 10, 100.0, "2026-08-01"),
                  _order("X", "sell", 4, 110.0, "2026-08-05"),
                  _order("X", "sell", 6, 90.0, "2026-08-06")]
        trips, unmatched = reviewer.build_round_trips(orders)
        self.assertEqual(len(trips), 2)
        self.assertEqual(unmatched, [])
        self.assertAlmostEqual(sum(t["qty"] for t in trips), 10)

    def test_sell_without_an_entry_is_flagged_not_scored(self):
        trips, unmatched = reviewer.build_round_trips(
            [_order("AMD", "sell", 8, 519.95, "2026-07-15")])
        self.assertEqual(trips, [])
        self.assertEqual(unmatched[0]["symbol"], "AMD")

    def test_quarantined_symbols_are_excluded(self):
        orders = [_order("LBGJ", "buy", 154903, 0.0288, "2026-07-24"),
                  _order("LBGJ", "sell", 154903, 3.05, "2026-08-21")]
        trips, _ = reviewer.build_round_trips(orders)
        self.assertEqual(trips, [], "a broker data fault is not a trading result")

    def test_metrics_are_computed_from_realized_pnl(self):
        orders = [_order("A", "buy", 1, 100.0, "2026-08-01"),
                  _order("A", "sell", 1, 110.0, "2026-08-02"),
                  _order("B", "buy", 1, 100.0, "2026-08-01"),
                  _order("B", "sell", 1, 95.0, "2026-08-02")]
        trips, _ = reviewer.build_round_trips(orders)
        m = reviewer.round_trip_metrics(trips)
        self.assertEqual(m["count"], 2)
        self.assertAlmostEqual(m["win_rate"], 50.0)
        self.assertAlmostEqual(m["profit_factor"], 2.0)


# ══════════════════════════════════════════════════════════════════════════════
# Graduation gate
# ══════════════════════════════════════════════════════════════════════════════
class TestGraduationGate(unittest.TestCase):

    def test_profitability_is_gated_not_just_process(self):
        for name in ("GRADUATION_MIN_PROFIT_FACTOR", "GRADUATION_MIN_RETURN_PCT",
                     "GRADUATION_MIN_ROUND_TRIPS", "GRADUATION_MIN_EXPECTANCY_PCT"):
            with self.subTest(criterion=name):
                self.assertTrue(hasattr(reviewer, name))

    def test_profit_factor_bar_is_above_breakeven(self):
        self.assertGreater(reviewer.GRADUATION_MIN_PROFIT_FACTOR, 1.0)

    def test_benchmark_comparison_is_required(self):
        self.assertTrue(reviewer.GRADUATION_MUST_BEAT_BENCHMARK)

    def test_lbgj_is_quarantined(self):
        self.assertIn("LBGJ", reviewer.QUARANTINED_SYMBOLS)

    def test_implausible_pnl_is_excluded_in_both_directions(self):
        self.assertGreater(reviewer.IMPLAUSIBLE_PNL_PCT, 0)
        self.assertLess(reviewer.TOTAL_LOSS_PNL_PCT, 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
