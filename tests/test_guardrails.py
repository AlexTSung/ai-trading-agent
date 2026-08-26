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
import research      # noqa: E402
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

    def test_rail2_observe_tier_applies_nothing(self):
        """Below the first tier nothing may change, but patterns still record."""
        self._write_trips(3)
        self._write_hints(scope="alpaca_equities", approve_threshold=8.0,
                          news_weight_adj=0.81)
        result = judge.load_learned_hints()
        self.assertEqual(result["applied"], {})
        self.assertEqual(result["tier"], "observe")

    def test_tier_ladder_scales_the_allowed_band(self):
        expected = [(3, "observe", 0.0), (5, "advisory", 0.03),
                    (10, "active", 0.06), (20, "active", 0.10),
                    (30, "confident", 0.15), (500, "confident", 0.15)]
        for sample, tier, band in expected:
            with self.subTest(sample=sample):
                self.assertEqual(judge.learning_tier(sample), (tier, band))

    def test_advisory_tier_records_weights_without_applying_them(self):
        self._write_trips(6)
        self._write_hints(scope="alpaca_equities", news_weight_adj=0.81)
        result = judge.load_learned_hints()
        self.assertEqual(result["tier"], "advisory")
        self.assertNotIn("news_weight_mult", result["applied"])
        self.assertTrue(any("ADVISORY" in n for n in result["notes"]))

    def test_weight_band_tightens_on_smaller_samples(self):
        """The same raw hint must move less when the evidence is thinner."""
        self._write_hints(scope="alpaca_equities", news_weight_adj=0.50)
        self._write_trips(10)
        at10 = judge.load_learned_hints()["applied"]["news_weight_mult"]
        self._write_trips(30)
        at30 = judge.load_learned_hints()["applied"]["news_weight_mult"]
        self.assertGreater(at10, at30, "10 trips must permit a smaller move than 30")
        self.assertAlmostEqual(at10, 0.94, places=2)
        self.assertAlmostEqual(at30, 0.85, places=2)

    def test_risk_reducing_gate_acts_from_the_advisory_tier(self):
        """Tightening on thin evidence is prudent; loosening never is."""
        self._write_trips(6)
        self._write_hints(scope="alpaca_equities", approve_threshold=8.0)
        self.assertEqual(
            judge.load_learned_hints()["applied"].get("watchlist_gate"), 8.0)

    def test_wilson_interval_widens_on_small_samples(self):
        lo5, hi5 = judge.wilson_interval(3, 5)
        lo50, hi50 = judge.wilson_interval(30, 50)
        self.assertGreater(hi5 - lo5, hi50 - lo50)
        self.assertGreater(hi5 - lo5, 0.35, "5 trips cannot distinguish skill from luck")

    def test_wilson_interval_rejects_nonsense(self):
        self.assertEqual(judge.wilson_interval(None, 10), (None, None))
        self.assertEqual(judge.wilson_interval(11, 10), (None, None))
        self.assertEqual(judge.wilson_interval(1, 0), (None, None))

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


# ══════════════════════════════════════════════════════════════════════════════
# Learning analytics — what the trade record can actually tell us
# ══════════════════════════════════════════════════════════════════════════════
import learning_analytics as la   # noqa: E402


def _trip(sym, pnl_pct, hold_days=10, entry_date="2026-08-01",
          exit_date="2026-08-11", entry=100.0):
    return {"symbol": sym, "entry_date": entry_date, "exit_date": exit_date,
            "entry": entry, "exit": entry * (1 + pnl_pct / 100),
            "pnl_pct": pnl_pct, "pnl_dollar": pnl_pct, "hold_days": hold_days,
            "tier": la.tier_of(sym)}


class TestAnalyticsSummary(unittest.TestCase):

    def test_summary_metrics(self):
        s = la.summarize([_trip("A", 10.0), _trip("B", -5.0)])
        self.assertEqual(s["n"], 2)
        self.assertAlmostEqual(s["win_rate"], 50.0)
        self.assertAlmostEqual(s["profit_factor"], 2.0)
        self.assertAlmostEqual(s["expectancy"], 2.5)

    def test_empty_input_is_safe(self):
        self.assertEqual(la.summarize([])["n"], 0)

    def test_confidence_labels_scale_with_n(self):
        self.assertEqual(la.confidence(2), "anecdote")
        self.assertEqual(la.confidence(6), "suggestive")
        self.assertEqual(la.confidence(25), "actionable")

    def test_tier_mapping_matches_the_executor(self):
        for sym in ("AAPL", "DDOG", "NOK", "SQQQ"):
            with self.subTest(symbol=sym):
                self.assertEqual(la.tier_of(sym), trade.tier_for(sym))


class TestAnalyticsQuestions(unittest.TestCase):
    """Each test is one of the questions the analytics module exists to answer."""

    def test_discovery_vs_watchlist_is_separated(self):
        trips = [dict(_trip("AAA", -8.0), is_discovery=True),
                 dict(_trip("BBB", -6.0), is_discovery=True),
                 dict(_trip("AAPL", 12.0), is_discovery=False)]
        out = la.analyse_source(trips)
        self.assertEqual(out["discovery"]["n"], 2)
        self.assertEqual(out["watchlist"]["n"], 1)
        self.assertLess(out["discovery"]["expectancy"],
                        out["watchlist"]["expectancy"])

    def test_stop_effectiveness_splits_stopped_from_survivors(self):
        trips = [dict(_trip("AAPL", -20.5), stopped_out=True),
                 dict(_trip("AAPL", 15.0), stopped_out=False)]
        out = la.analyse_tier_stops(trips)
        self.assertEqual(out["TIER1"]["stopped_out"]["n"], 1)
        self.assertEqual(out["TIER1"]["not_stopped"]["n"], 1)
        self.assertAlmostEqual(out["TIER1"]["stopped_share"], 0.5)

    def test_cutting_winners_early_is_detectable(self):
        """Winners held far shorter than losers is the pattern to surface."""
        trips = [_trip("A", 8.0, hold_days=2), _trip("B", 6.0, hold_days=3),
                 _trip("C", -9.0, hold_days=40), _trip("D", -7.0, hold_days=35)]
        out = la.analyse_hold_time(trips)
        self.assertLess(out["avg_hold_winners"], out["avg_hold_losers"])

    def test_judge_score_correlation_is_computed(self):
        trips = [dict(_trip("A", 10.0), judge_score=9.0),
                 dict(_trip("B", 5.0), judge_score=8.0),
                 dict(_trip("C", -5.0), judge_score=7.0)]
        out = la.analyse_judge_calibration(trips)
        self.assertIsNotNone(out["correlation"])
        self.assertGreater(out["correlation"], 0.9, "monotonic data must correlate")

    def test_pearson_handles_degenerate_input(self):
        self.assertEqual(la.pearson([1, 2], [1, 2])[0], None, "n<3 is undefined")
        self.assertEqual(la.pearson([1, 1, 1], [1, 2, 3])[0], None, "zero variance")

    def test_regime_split(self):
        trips = [_trip("A", 5.0, entry_date="2026-08-01"),
                 _trip("B", -5.0, entry_date="2026-08-02")]
        regime = {"2026-08-01": "RISK_ON", "2026-08-02": "RISK_OFF"}
        out = la.analyse_regime(trips, regime)
        self.assertEqual(out["RISK_ON"]["n"], 1)
        self.assertEqual(out["RISK_OFF"]["n"], 1)


class TestVetoOpportunityCost(unittest.TestCase):
    """
    With almost every candidate vetoed, forward returns on rejected names are
    the only evidence about whether the gates are calibrated or just too tight.
    """

    def test_forward_return_is_measured_in_calendar_days(self):
        prices = {"X": {"2026-08-03": 100.0, "2026-08-08": 90.0}}
        vetoes = [{"date": "2026-08-03", "symbol": "X", "reason": "chase"}]
        out = la.analyse_veto_cost(vetoes, prices, horizons=(5,))
        self.assertAlmostEqual(out["chase"]["5d"]["avg_forward_return"], -10.0)
        self.assertEqual(out["chase"]["5d"]["avg_actual_gap_days"], 5)

    def test_sparse_history_does_not_mislabel_a_distant_observation(self):
        """
        Indexing "the 5th later observation" compared a 5-day horizon against a
        50-day gap on the real, sparse research history. Out-of-tolerance
        observations must be dropped, not relabelled.
        """
        prices = {"X": {"2026-06-30": 100.0, "2026-08-21": 130.0}}
        vetoes = [{"date": "2026-06-30", "symbol": "X", "reason": "chase"}]
        out = la.analyse_veto_cost(vetoes, prices, horizons=(5,))
        self.assertEqual(out, {}, "a 52-day gap cannot stand in for a 5-day horizon")

    def test_nearest_date_respects_tolerance(self):
        series = {"2026-08-01": 1.0, "2026-08-06": 1.0, "2026-08-30": 1.0}
        self.assertEqual(la._nearest_date(series, "2026-08-01", 5), "2026-08-06")
        self.assertIsNone(la._nearest_date(series, "2026-08-01", 100))

    def test_veto_reasons_are_classified(self):
        cases = [("CHASE VETO: RSI(14) 83 is above 60", "chase"),
                 ("EARNINGS VETO: NVDA flagged", "earnings"),
                 ("Discovery quality veto: scored 0.0/2.5", "discovery_quality"),
                 ("CASH BELOW 20% FLOOR (12%)", "cash_floor"),
                 ("something unrecognised", "other")]
        for text, expected in cases:
            with self.subTest(text=text[:24]):
                self.assertEqual(la._veto_class(text), expected)

    def test_report_renders_with_no_data(self):
        """The EOD pipeline must not crash on an empty record."""
        rendered = la.render(la.build(round_trips=[]))
        self.assertIn("Trade Learnings", rendered)


# ══════════════════════════════════════════════════════════════════════════════
# Options — same failure classes as the equities side, previously untested
# ══════════════════════════════════════════════════════════════════════════════
import options_strategy as ostrat    # noqa: E402
import options_monitor as omon       # noqa: E402


class TestOptionsRiskFilters(unittest.TestCase):

    # Credit structures carry POSITIVE theta in this codebase's convention.
    # Legs are present because passes_risk_filters now validates the structure
    # against its own no-arbitrage bounds before anything else, and a dict with
    # no legs has no bounds to check. Credit ($1.20) sits below the strike width
    # ($5.20), so the vertical is internally consistent.
    DEFINED = {"strategy": "bull_put_spread", "max_loss": 4.0,
               "reward_risk": 0.30, "theta": 0.0135, "dte": 40,
               "max_profit": 1.20, "net_credit": 1.20,
               "legs": [{"type": "short_put", "strike": 95.0, "price": 2.00},
                        {"type": "long_put",  "strike": 89.8, "price": 0.80}]}

    # Tight quotes (2%-of-mid spreads, typical of a liquid 45-DTE contract on an
    # index ETF or mega-cap). Supplied wherever a test targets a gate OTHER than
    # execution cost, so the cost haircut does not silently become the thing
    # under test.
    @staticmethod
    def _tight_quotes(strat, spread_frac=0.02):
        return [{"bid": leg["price"] * (1 - spread_frac / 2),
                 "ask": leg["price"] * (1 + spread_frac / 2)}
                for leg in strat["legs"]]

    def test_unknown_equity_is_refused_not_guessed(self):
        """
        The 2% cap defaulted to a hardcoded $25,000 account. Sizing risk against
        an account that does not exist is worse than refusing to size it.
        """
        for bad in (None, 0, -1):
            with self.subTest(equity=bad):
                ok, why = ostrat.passes_risk_filters(self.DEFINED, account_equity=bad)
                self.assertFalse(ok)
                self.assertIn("equity unknown", why.lower())

    def test_undefined_max_loss_is_rejected(self):
        """short_strangle reports max_loss None — unlimited. It must not pass."""
        strangle = ostrat.build_short_strangle(S=100.0, T=40/365, sigma=0.30)
        strangle["strategy"] = "short_strangle"
        self.assertIsNone(strangle["max_loss"], "strangle risk is unbounded")
        ok, why = ostrat.passes_risk_filters(strangle, account_equity=91000.0)
        self.assertFalse(ok)
        self.assertIn("max loss", why.lower())

    def test_two_percent_cap_scales_with_equity(self):
        """A $4.00-wide spread is $400 per contract: fine at $91K, not at $10K."""
        q = self._tight_quotes(self.DEFINED)
        ok_big, why_big = ostrat.passes_risk_filters(
            self.DEFINED, account_equity=91000.0, leg_quotes=q)
        ok_small, why = ostrat.passes_risk_filters(
            self.DEFINED, account_equity=10000.0, leg_quotes=q)
        self.assertTrue(ok_big, why_big)
        self.assertFalse(ok_small)
        self.assertIn("2%", why)

    def test_phantom_equity_would_have_inflated_the_cap_sixfold(self):
        reported, honest = 559288.0, 91295.0
        self.assertGreater(reported * 0.02, 10000)
        self.assertLess(honest * 0.02, 2000)

    def test_credit_structures_are_not_rejected_for_having_positive_theta(self):
        """
        The theta condition was inverted: `theta >= 0` rejected, so every iron
        condor, put spread, call spread and strangle was refused by a filter
        whose own message said positive theta was what it wanted. The options
        pipeline could not open a single premium-selling position.
        """
        for name, builder in (("iron_condor", ostrat.build_iron_condor),
                              ("bull_put_spread", ostrat.build_bull_put_spread),
                              ("bear_call_spread", ostrat.build_bear_call_spread)):
            with self.subTest(strategy=name):
                strat = builder(S=100.0, T=40 / 365, sigma=0.30)
                strat["strategy"] = name
                self.assertGreater(strat["theta"], 0, "credit theta must be positive")
                ok, why = ostrat.passes_risk_filters(
                    strat, account_equity=91000.0,
                    leg_quotes=self._tight_quotes(strat))
                self.assertTrue(ok, f"{name} rejected: {why}")

    def test_missing_dte_is_refused_not_crashed(self):
        """`None < 21` raises TypeError rather than failing the check."""
        no_dte = dict(self.DEFINED)
        no_dte.pop("dte")
        ok, why = ostrat.passes_risk_filters(
            no_dte, account_equity=91000.0,
            leg_quotes=self._tight_quotes(no_dte))
        self.assertFalse(ok)
        self.assertIn("DTE", why)

    def test_entry_window_is_enforced(self):
        for dte, should_pass in ((15, False), (40, True), (75, False)):
            with self.subTest(dte=dte):
                strat = dict(self.DEFINED, dte=dte)
                ok, why = ostrat.passes_risk_filters(
                    strat, account_equity=91000.0,
                    leg_quotes=self._tight_quotes(strat))
                self.assertEqual(ok, should_pass, why)

    def test_weak_reward_risk_is_rejected(self):
        weak = dict(self.DEFINED, reward_risk=0.10, max_profit=0.40,
                    net_credit=0.40)
        ok, why = ostrat.passes_risk_filters(
            weak, account_equity=91000.0, leg_quotes=self._tight_quotes(weak))
        self.assertFalse(ok)
        self.assertIn("0.25", why)


class TestMacroRegimeSizing(unittest.TestCase):
    """
    CLAUDE.md's Dalio rule — "In risk-off: reduce max long position size from 5%
    to 2.5%, require STRONG_BUY only" — was documented and announced but never
    implemented. score_macro returned 0.0 and printed "(half sizing applies)"
    while `size_multiplier` came only from the VIX ladder, so the Judge was
    reporting a risk control that did not exist and the book ran full size into a
    downtrend. That is the phantom-risk failure mode, and it is the same class of
    defect as check_option_liquidity having zero call sites.
    """

    def test_risk_on_preserves_conviction_tiers(self):
        """Hard Rule 6's 8/5/3% must survive — the ceiling is risk-off only."""
        for conviction in (0.08, 0.05, 0.03):
            for regime in ("RISK_ON", "NEUTRAL"):
                with self.subTest(conviction=conviction, regime=regime):
                    pct, _ = trade.regime_adjusted_position_pct(conviction, regime, 1.0)
                    self.assertAlmostEqual(pct, conviction)

    def test_risk_off_caps_at_two_and_a_half_percent(self):
        for conviction in (0.08, 0.05, 0.03):
            with self.subTest(conviction=conviction):
                pct, notes = trade.regime_adjusted_position_pct(
                    conviction, "RISK_OFF", 1.0)
                self.assertAlmostEqual(pct, trade.RISK_OFF_MAX_POSITION_PCT)
                self.assertTrue(any("RISK-OFF" in n for n in notes))

    def test_ceiling_is_a_min_not_an_assignment(self):
        """
        THE subtle bug to avoid. If the VIX ladder already cut a 3% position to
        1.5%, entering risk-off must not raise it back to the 2.5% ceiling.
        Combining two risk reducers can never increase exposure.
        """
        pct, _ = trade.regime_adjusted_position_pct(0.03, "RISK_OFF", 0.5)
        self.assertAlmostEqual(pct, 0.015)
        self.assertLess(pct, trade.RISK_OFF_MAX_POSITION_PCT)

    def test_risk_reducers_compose_monotonically(self):
        """No combination of regime and VIX may exceed the plain conviction size."""
        for conviction in (0.08, 0.05, 0.03):
            base, _ = trade.regime_adjusted_position_pct(conviction, "RISK_ON", 1.0)
            for regime in ("RISK_ON", "NEUTRAL", "RISK_OFF"):
                for vixm in (1.0, 0.5, 0.25):
                    with self.subTest(conviction=conviction, regime=regime, vix=vixm):
                        pct, _ = trade.regime_adjusted_position_pct(
                            conviction, regime, vixm)
                        self.assertLessEqual(pct, base + 1e-12)

    def test_the_naive_formula_would_have_flattened_conviction(self):
        """
        Guards against the proposed fix, not just the original bug. Treating a
        multiplier as a fraction of capital and applying the 2.5% cap outside the
        risk-off branch collapses every tier to 2.5% in EVERY regime.
        """
        def naive(base_capital, vix_multiplier, judge_signal):
            final = vix_multiplier
            if judge_signal == "RISK_OFF":
                final = vix_multiplier * 0.5
            return min(base_capital * final, base_capital * 0.025)

        cap = 91118.79
        sizes = {naive(cap, v, r)
                 for v in (1.0, 0.5) for r in ("RISK_ON", "RISK_OFF")}
        self.assertEqual(len(sizes), 1, "naive form gives one size for everything")
        self.assertAlmostEqual(sizes.pop(), cap * 0.025, places=2)

        ours = {round(trade.regime_adjusted_position_pct(c, r, v)[0], 6)
                for c in (0.08, 0.05, 0.03)
                for r in ("RISK_ON", "RISK_OFF") for v in (1.0, 0.5)}
        self.assertGreater(len(ours), 1, "ours must still discriminate conviction")

    def test_unknown_regime_does_not_unlock_full_size_silently(self):
        """An absent regime is NEUTRAL, and NEUTRAL never exceeds conviction."""
        pct, _ = trade.regime_adjusted_position_pct(0.08, "NEUTRAL", 1.0)
        self.assertAlmostEqual(pct, 0.08)
        self.assertEqual(judge.macro_regime({}), "NEUTRAL",
                         "missing SPY data must not read as RISK_ON")

    def test_regime_is_derived_from_the_same_branch_as_the_score(self):
        """
        One source of truth. The report saying "half sizing applies" while the
        executor sized full is exactly what splitting them caused.
        """
        cases = [
            ({"signal": "WEAK_BUY",    "ma20": 750.0, "ma50": 740.0}, "RISK_ON"),
            ({"signal": "STRONG_SELL", "ma20": 745.87, "ma50": 745.02}, "RISK_OFF"),
            ({"signal": "HOLD",        "ma20": 740.0, "ma50": 750.0}, "RISK_OFF"),
            ({"signal": "HOLD",        "ma20": 750.0, "ma50": 740.0}, "NEUTRAL"),
        ]
        for tech, expected in cases:
            research_blob = {"symbols": {"SPY": {"technicals": tech}}}
            with self.subTest(tech=tech):
                score, _ = judge.score_macro(research_blob)
                regime = judge.macro_regime(research_blob)
                self.assertEqual(regime, expected)
                if regime == "RISK_OFF":
                    self.assertEqual(score, 0.0)
                elif regime == "RISK_ON":
                    self.assertEqual(score, 1.5)

    def test_risk_off_requires_strong_buy(self):
        self.assertEqual(trade.RISK_OFF_REQUIRED_SIGNAL, "STRONG_BUY")

    def test_weaker_signals_are_blocked_before_sizing_in_risk_off(self):
        """
        A trade that must not happen should not be sized. The gate runs before
        calculate_max_shares, so no order is generated at all.
        """
        with mock.patch.object(trade, "get_market_status", return_value={"is_open": True}), \
             mock.patch.object(trade, "is_buyable", return_value=(True, "ok")), \
             mock.patch.object(trade, "get_latest_ask", return_value=100.0), \
             mock.patch.object(trade, "calculate_max_shares") as sizer, \
             mock.patch.object(trade, "place_limit_order") as placer:
            for signal in ("WEAK_BUY", "HOLD", "BUY"):
                with self.subTest(signal=signal):
                    out = trade.buy_with_guardrails(
                        "CEG", signal, {"cash": "70000", "buying_power": "70000"},
                        research_limit_price=99.0, judge_score=8.0,
                        macro_regime="RISK_OFF", positions=[])
                    self.assertEqual(out.get("action"), "SKIPPED")
                    self.assertIn("REGIME BLOCK", out.get("error", ""))
            sizer.assert_not_called()
            placer.assert_not_called()

    def test_strong_buy_still_passes_the_regime_gate_in_risk_off(self):
        with mock.patch.object(trade, "get_market_status", return_value={"is_open": True}), \
             mock.patch.object(trade, "is_buyable", return_value=(True, "ok")), \
             mock.patch.object(trade, "get_latest_ask", return_value=100.0), \
             mock.patch.object(trade, "calculate_max_shares",
                               return_value=(10, "sized")), \
             mock.patch.object(trade, "place_limit_order",
                               return_value={"_ok": True, "id": "o1"}):
            out = trade.buy_with_guardrails(
                "CEG", "STRONG_BUY", {"cash": "70000", "buying_power": "70000"},
                research_limit_price=99.0, judge_score=8.0,
                macro_regime="RISK_OFF", positions=[])
        self.assertEqual(out.get("action"), "BUY")

    def test_risk_on_does_not_gate_on_signal_strength(self):
        """The STRONG_BUY-only requirement is a risk-off rule, not a global one."""
        with mock.patch.object(trade, "get_market_status", return_value={"is_open": True}), \
             mock.patch.object(trade, "is_buyable", return_value=(True, "ok")), \
             mock.patch.object(trade, "get_latest_ask", return_value=100.0), \
             mock.patch.object(trade, "calculate_max_shares",
                               return_value=(10, "sized")), \
             mock.patch.object(trade, "place_limit_order",
                               return_value={"_ok": True, "id": "o1"}):
            out = trade.buy_with_guardrails(
                "CEG", "WEAK_BUY", {"cash": "70000", "buying_power": "70000"},
                research_limit_price=99.0, judge_score=8.0,
                macro_regime="RISK_ON", positions=[])
        self.assertEqual(out.get("action"), "BUY")

    def test_vix_block_still_takes_precedence(self):
        """Regression: the regime gate must not have displaced the VIX 35 block."""
        with mock.patch.object(trade, "get_market_status", return_value={"is_open": True}), \
             mock.patch.object(trade, "is_buyable", return_value=(True, "ok")), \
             mock.patch.object(trade, "get_latest_ask", return_value=100.0):
            out = trade.buy_with_guardrails(
                "CEG", "STRONG_BUY", {"cash": "70000", "buying_power": "70000"},
                research_limit_price=99.0, judge_score=8.0,
                size_multiplier=0.0, macro_regime="RISK_ON", positions=[])
        self.assertEqual(out.get("action"), "SKIPPED")
        self.assertIn("VOLATILITY BLOCK", out.get("error", ""))


class TestRestingEntryOrders(unittest.TestCase):
    """
    Entry limits were time-in-force "day", so a patient bid at MA20 x 0.995
    expired at the close every session it was not touched. On a name trading 5%
    above its MA20 that is every session — cancel_orphan_exits existed
    specifically to clean up the abandoned OCO legs of "day-limit buys that
    expired unfilled". Five sessions to 2026-08-26 produced zero fills.

    GTC lets the bid rest. The limit price is unchanged, so this is strictly more
    patience, not a worse entry. The new risk is a stale bid filling after the
    thesis broke, which is what revalidate_resting_entries guards.
    """

    def _order(self, sym="CEG", days_old=0, cid=None):
        ts = (datetime.now() - timedelta(days=days_old)).isoformat()
        return {"id": f"ord-{sym}", "symbol": sym, "side": "buy",
                "client_order_id": cid if cid is not None else f"agent-buy-{sym}-x",
                "submitted_at": ts}

    def _research(self, sym="CEG", ma20=100.0, ma50=90.0, earnings=()):
        return {"symbols": {sym: {"technicals": {"ma20": ma20, "ma50": ma50}}},
                "intelligence": {"earnings_watch_symbols": list(earnings)}}

    def test_entries_rest_rather_than_expiring_nightly(self):
        self.assertEqual(trade.ENTRY_ORDER_TIF, "gtc",
                         "a day order cannot catch a pullback that arrives tomorrow")

    def test_a_valid_bid_is_kept(self):
        keep, why = trade.resting_entry_verdict(self._order(), self._research())
        self.assertTrue(keep, why)

    def test_stale_bid_is_cancelled(self):
        keep, why = trade.resting_entry_verdict(
            self._order(days_old=trade.ENTRY_ORDER_MAX_AGE_DAYS + 1), self._research())
        self.assertFalse(keep)
        self.assertIn("max age", why)

    def test_bid_is_cancelled_when_earnings_approach(self):
        """A resting bid would fill straight into the print (Hard Rule 9)."""
        keep, why = trade.resting_entry_verdict(
            self._order(), self._research(earnings=("CEG",)))
        self.assertFalse(keep)
        self.assertIn("earnings", why.lower())

    def test_bid_is_cancelled_on_thesis_break(self):
        """MA20 below MA50 is no longer a pullback in an uptrend."""
        keep, why = trade.resting_entry_verdict(
            self._order(), self._research(ma20=90.0, ma50=100.0))
        self.assertFalse(keep)
        self.assertIn("thesis break", why.lower())

    def test_chase_veto_does_not_cancel_a_resting_bid(self):
        """
        A chase veto means price rose AWAY from our bid. A resting bid below the
        market is exactly the order we want then — cancelling on it would mean
        only ever bidding for stocks that are already falling.
        """
        r = self._research(ma20=100.0, ma50=90.0)
        r["symbols"]["CEG"]["technicals"].update({"rsi": 78, "last_close": 130.0})
        keep, why = trade.resting_entry_verdict(self._order(), r)
        self.assertTrue(keep, f"should still rest: {why}")

    def test_unreadable_timestamp_cancels_rather_than_assuming_fresh(self):
        bad = self._order()
        bad["submitted_at"] = "not-a-date"
        keep, why = trade.resting_entry_verdict(bad, self._research())
        self.assertFalse(keep)
        self.assertIn("age", why.lower())

    def test_only_agent_placed_buys_are_touched(self):
        """A manual or third-party order is not ours to cancel."""
        with mock.patch.object(trade.requests, "delete") as d:
            actions = trade.revalidate_resting_entries(
                [self._order(days_old=99, cid="manual-order-1"),
                 dict(self._order(), side="sell")],
                self._research())
        self.assertEqual(actions, [])
        d.assert_not_called()

    def test_cancelled_orders_are_reported_with_a_reason(self):
        with mock.patch.object(trade.requests, "delete") as d:
            d.return_value = mock.Mock(status_code=204)
            actions = trade.revalidate_resting_entries(
                [self._order(days_old=99)], self._research())
        self.assertEqual(len(actions), 1)
        self.assertEqual(actions[0]["action"], "CANCELLED")
        self.assertIn("reason", actions[0])


class TestEntryLimitReachability(unittest.TestCase):
    """
    The limit price is Hard Rule 2's formula (MA20 x 0.995) and is unchanged.
    What was missing is any measure of whether that price is likely to trade. A
    percentage gap is not comparable across a 1%-ATR utility and a 4%-ATR growth
    name; ATR is the average daily range, so the gap measured in ATRs answers
    "how many normal days of movement away is this".
    """

    def test_gap_is_measured_in_atr_not_just_percent(self):
        """
        The same 5% gap is five normal days for a 1%-ATR utility and just over
        one day for a 4%-ATR growth name. Percent alone cannot tell them apart.
        """
        _, quiet_dist, quiet_label = research.entry_limit_reachability(100.0, 95.0, 1.0)
        _, wild_dist,  wild_label  = research.entry_limit_reachability(100.0, 95.0, 4.0)
        self.assertEqual(quiet_dist, 5.0)
        self.assertEqual(wild_dist, 1.25)
        self.assertEqual(quiet_label, "UNLIKELY")
        self.assertEqual(wild_label, "PLAUSIBLE")

    def test_labels_against_the_live_2026_08_26_values(self):
        cases = [
            # (last, ma20, atr, expected label)
            (321.57, 311.35, 7.4043, "PLAUSIBLE"),   # AAPL, 3.7% gap = 1.59 ATR
            (244.43, 255.92, 9.00,   "AT_OR_BELOW"), # DDOG, already below MA20
            (208.76, 202.73, 7.2814, "LIKELY"),      # NVDA, 3.0% above MA20
            (100.00, 90.00,  1.00,   "UNLIKELY"),    # 10% away, quiet name
        ]
        for last, ma20, atr, expected in cases:
            limit = round(ma20 * 0.995, 2)
            with self.subTest(last=last, ma20=ma20, atr=atr):
                _, _, label = research.entry_limit_reachability(last, limit, atr)
                self.assertEqual(label, expected)

    def test_at_or_below_takes_precedence_over_atr(self):
        """Price already under the target is a value entry, not a wait."""
        gap, _, label = research.entry_limit_reachability(95.0, 99.50, 2.0)
        self.assertLessEqual(gap, 0)
        self.assertEqual(label, "AT_OR_BELOW")

    def test_missing_atr_is_unknown_not_a_guess(self):
        """A zero or absent ATR must not silently become LIKELY."""
        for atr in (0, None, "n/a"):
            with self.subTest(atr=atr):
                _, dist, label = research.entry_limit_reachability(100.0, 95.0, atr)
                self.assertIsNone(dist)
                self.assertEqual(label, "UNKNOWN")

    def test_degenerate_price_is_refused(self):
        for last, limit in ((0, 95.0), (-1, 95.0), (100.0, 0), (100.0, None)):
            with self.subTest(last=last, limit=limit):
                self.assertEqual(
                    research.entry_limit_reachability(last, limit, 2.0)[2], "UNKNOWN")

    def test_limit_formula_is_unchanged(self):
        """
        Hard Rule 2's entry price is MA20 x 0.995. Reachability reporting must
        not have moved the price itself — that would be chasing by another name.
        """
        self.assertAlmostEqual(round(311.35 * 0.995, 2), 309.79, places=2)


class TestMacroRegimeMessage(unittest.TestCase):
    """
    On 2026-08-26 the Judge reported "RISK-OFF — SPY MA20 $745.87 < MA50
    $745.02". MA20 was ABOVE MA50; the real trigger was SPY's STRONG_SELL signal
    (price 738.06, below the MA50). The verdict was defensible, the stated reason
    was false, and a reader of the journal would conclude the MAs had crossed.
    """

    def _r(self, signal, ma20, ma50):
        return {"symbols": {"SPY": {"technicals": {
            "signal": signal, "ma20": ma20, "ma50": ma50}}}}

    def test_the_live_case_no_longer_claims_a_cross_that_did_not_happen(self):
        score, note = judge.score_macro(self._r("STRONG_SELL", 745.87, 745.02))
        self.assertEqual(score, 0.0, "STRONG_SELL is still risk-off")
        self.assertNotIn("$745.87 < MA50", note)
        self.assertIn("STRONG_SELL", note)

    def test_a_genuine_cross_is_still_described_as_a_cross(self):
        _, note = judge.score_macro(self._r("HOLD", 740.0, 750.0))
        self.assertIn("<", note)
        self.assertIn("RISK-OFF", note)

    def test_risk_on_is_unchanged(self):
        score, note = judge.score_macro(self._r("WEAK_BUY", 750.0, 740.0))
        self.assertEqual(score, 1.5)
        self.assertIn("RISK-ON", note)


class TestWatchlistExpansion(unittest.TestCase):
    """
    The watchlist was ~100% mega-cap tech plus inverse ETFs. On 2026-08-26 every
    tech name showed MA20 < MA50 while energy (+22% YTD), consumer defensives
    (+13.3%), industrials and utilities led. Five sessions produced zero buys
    because the universe had no pullback setups — not because the gates misfired.
    """

    NEW = ("CEG", "VST", "GEV", "LLY", "MRK", "ARGX", "COP", "DTM", "DT", "TGT")

    def setUp(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "watchlist.json")
        with open(path) as f:
            self.cfg = json.load(f)
        self.by_sym = {e["symbol"]: e for e in self.cfg["watchlist"]}

    def test_all_ten_candidates_are_present(self):
        for sym in self.NEW:
            with self.subTest(symbol=sym):
                self.assertIn(sym, self.by_sym)

    def test_each_new_name_is_tradeable_and_capped(self):
        for sym in self.NEW:
            e = self.by_sym[sym]
            with self.subTest(symbol=sym):
                self.assertGreater(e["max_allocation_pct"], 0,
                                   "a 0% cap would silently make it unbuyable")
                self.assertLessEqual(e["max_allocation_pct"], 8,
                                     "Hard Rule 6 caps a single position at 8%")

    def test_high_beta_names_are_not_left_on_the_speculative_stop(self):
        """
        Tier 3's 10% stop is defined for "speculative, market cap < $10B". A
        $60B+ utility or a $700B pharma defaulting into it was a
        misclassification — and a 10% stop on a high-ATR name like GEV is taken
        out by ordinary noise.
        """
        for sym in ("CEG", "VST", "GEV", "LLY", "MRK", "ARGX", "DT"):
            with self.subTest(symbol=sym):
                self.assertEqual(trade.tier_for(sym), "TIER2")
                self.assertEqual(trade.stop_pct_for(sym), trade.STOP_PCT_TIER2)

    def test_correlated_pairs_are_flagged_in_config(self):
        """CEG and VST are one bet; DT is the same industry as the open DDOG."""
        self.assertEqual(self.by_sym["CEG"].get("correlation_group"),
                         self.by_sym["VST"].get("correlation_group"))
        self.assertIsNotNone(self.by_sym["CEG"].get("correlation_group"))
        self.assertIn("DDOG", self.by_sym["DT"]["notes"],
                      "the DDOG overlap has to be visible where sizing happens")

    def test_the_universe_is_no_longer_only_tech(self):
        self.assertGreaterEqual(len(self.cfg["watchlist"]), 24)

    def test_inverse_etfs_keep_their_tight_stop(self):
        """Regression: the tier edit must not have disturbed the 5% inverse stop."""
        for sym in ("SQQQ", "SPXS", "SOXS"):
            with self.subTest(symbol=sym):
                self.assertEqual(trade.tier_for(sym), "INVERSE")

    def test_tier1_compounders_are_unchanged(self):
        for sym in ("AAPL", "MSFT", "GOOGL", "AMZN", "NVDA"):
            with self.subTest(symbol=sym):
                self.assertEqual(trade.tier_for(sym), "TIER1")


class TestRoundTripWindow(unittest.TestCase):
    """
    The graduation round-trip count could never rise above zero.

    fetch_filled_orders(since) filters on Alpaca's `after`, which matches
    submitted_at, not fill time. Passing the Phase 2B baseline straight through
    returned only orders SUBMITTED after 2026-08-20 — but every position open at
    the baseline was bought in June or July, so build_round_trips() saw sells
    with no open lot and dropped them all into `unmatched`.

    Live consequence: NVDA closed 2026-08-26 at +9.06%, the first realized Phase
    2B trade and a winner, and counted zero. Occurrences would have read 0 on the
    2026-09-03 decision date regardless of how the agent traded.
    """

    BUY_JUN = {"symbol": "NVDA", "side": "buy", "filled_qty": "25",
               "filled_avg_price": "192.81", "filled_at": "2026-06-29T13:31:00Z",
               "status": "filled", "id": "buy-jun"}
    SELL_AUG = {"symbol": "NVDA", "side": "sell", "filled_qty": "25",
                "filled_avg_price": "210.28", "filled_at": "2026-08-26T14:26:43Z",
                "status": "filled", "id": "sell-aug"}

    def test_the_bug_a_short_window_loses_the_entry_leg(self):
        """Baseline behaviour: fetching only post-baseline orders finds no trip."""
        trips, unmatched = reviewer.build_round_trips([self.SELL_AUG])
        self.assertEqual(trips, [], "no buy leg in window, so no round trip")
        self.assertEqual(len(unmatched), 1, "the winner lands in unmatched")

    def test_full_history_matches_the_trip(self):
        trips, unmatched = reviewer.build_round_trips([self.BUY_JUN, self.SELL_AUG])
        self.assertEqual(len(trips), 1)
        self.assertEqual(unmatched, [])
        t = trips[0]
        self.assertEqual(t["entry_date"], "2026-06-29")
        self.assertEqual(t["exit_date"], "2026-08-26")
        self.assertAlmostEqual(t["pnl_pct"], 9.06, places=1)
        self.assertAlmostEqual(t["pnl_dollar"], 436.75, places=2)

    def test_window_filters_on_exit_date_not_entry_date(self):
        """
        A position bought in July and sold in Phase 2B is a Phase 2B result —
        the exit is the decision being scored. Filtering on entry would discard
        it; filtering on exit keeps it.
        """
        with mock.patch.object(reviewer, "fetch_filled_orders",
                               return_value=[self.BUY_JUN, self.SELL_AUG]):
            in_window, all_trips, unmatched = reviewer.round_trips_closed_since(
                "2026-08-20")
        self.assertEqual(len(all_trips), 1)
        self.assertEqual(len(in_window), 1,
                         "exit on 2026-08-26 is inside a window opening 08-20")
        self.assertEqual(unmatched, [])

    def test_fetch_is_called_with_the_lookback_not_the_window(self):
        """
        THE bug, asserted directly. Mocking only the return value is not enough:
        a mock that ignores its argument passes whether the code fetches from the
        lookback or from `since`, so it cannot detect a regression to the short
        window. Assert the argument.
        """
        seen = {}

        def fake(since):
            seen["since"] = since
            # Mimic Alpaca: `after` filters on submitted_at, so a narrow window
            # returns only the sell and the entry leg is invisible.
            return [o for o in (self.BUY_JUN, self.SELL_AUG)
                    if o["filled_at"][:10] >= since]

        with mock.patch.object(reviewer, "fetch_filled_orders", side_effect=fake):
            in_window, all_trips, unmatched = reviewer.round_trips_closed_since(
                "2026-08-20")

        self.assertEqual(seen["since"], reviewer.ROUND_TRIP_LOOKBACK_START,
                         "must fetch the full history, not the reporting window")
        self.assertEqual(len(in_window), 1, "the NVDA winner must be counted")
        self.assertEqual(unmatched, [], "no orphaned sell once the buy is in scope")

    def test_trips_closed_before_the_window_are_excluded(self):
        """The window still has to mean something — old exits must not count."""
        early_sell = dict(self.SELL_AUG, filled_at="2026-07-15T14:00:00Z",
                          id="sell-jul")
        with mock.patch.object(reviewer, "fetch_filled_orders",
                               return_value=[self.BUY_JUN, early_sell]):
            in_window, all_trips, _ = reviewer.round_trips_closed_since(
                "2026-08-20")
        self.assertEqual(len(all_trips), 1)
        self.assertEqual(in_window, [], "exit predates the window")

    def test_lookback_reaches_back_far_enough_for_open_positions(self):
        """
        Every position open at the baseline was bought in June. A lookback that
        started at the baseline would reintroduce the original bug.
        """
        self.assertLess(reviewer.ROUND_TRIP_LOOKBACK_START,
                        reviewer.PHASE2B_BASELINE_DATE)
        self.assertLessEqual(reviewer.ROUND_TRIP_LOOKBACK_START, "2026-06-16",
                            "must predate the first paper fill (2026-06-16)")

    def test_lookback_never_shortens_the_requested_window(self):
        """A caller asking for an older window must not get a newer fetch."""
        captured = {}

        def fake(since):
            captured["since"] = since
            return []

        with mock.patch.object(reviewer, "fetch_filled_orders", side_effect=fake):
            reviewer.round_trips_closed_since("2026-01-01")
        self.assertEqual(captured["since"], "2026-01-01")

    def test_metrics_see_the_recovered_trip(self):
        trips, _ = reviewer.build_round_trips([self.BUY_JUN, self.SELL_AUG])
        m = reviewer.round_trip_metrics(trips)
        self.assertEqual(m["count"], 1)
        self.assertEqual(m["win_rate"], 100.0)
        self.assertAlmostEqual(m["net_dollar"], 436.75, places=2)
        self.assertEqual(m["avg_hold_days"], 58)


class TestOptionsExecutionCost(unittest.TestCase):
    """
    Every price in options_strategy is a Black-Scholes mid. Until now the risk
    gates measured reward/risk against those mids, so they gated on a price the
    account could never transact at.

    Wang, Pennock, Rothschild & Devanur (ICAIF '24) §4.3 measured a mean 78-cent
    bid-ask spread across the option series of the 30 DJI constituents. A
    four-leg structure crosses eight spreads over a round trip, and options
    positions here are closed at 21 DTE or +50%, not held to expiry.
    """

    def _condor(self, S=150.0, sigma=0.25):
        strat = ostrat.build_iron_condor(S=S, T=45 / 365, sigma=sigma)
        strat["symbol"] = "TEST"
        strat["underlying"] = S
        return strat

    def test_cost_is_charged_on_both_the_open_and_the_close(self):
        """A position that is closed pays the spread twice, not once."""
        strat = self._condor()
        cost = ostrat.execution_cost(strat)
        one_way = sum(leg["half_spread"] for leg in cost["per_leg"])
        self.assertAlmostEqual(cost["cost_per_share"], one_way * 2, places=4)
        self.assertEqual(ostrat.ROUND_TRIP_CROSSINGS, 2)

    def test_ratio_legs_are_charged_per_contract(self):
        """
        A butterfly's middle leg is two short contracts. Counting it once
        understated the cost of the single widest leg in the structure.
        """
        fly = ostrat.build_butterfly_spread(S=150.0, T=45 / 365, sigma=0.25)
        self.assertEqual(ostrat._leg_count(fly), 4, "1 + 2 + 1 contracts")
        mid_leg = next(l for l in ostrat.execution_cost(fly)["per_leg"]
                       if l["type"] == "short_call2")
        self.assertEqual(mid_leg["contracts"], 2)

    def test_measured_source_requires_a_quote_for_every_leg(self):
        """
        A cost that is half measured and half estimated is neither. Reporting it
        as measured would let the liquidity floor run against a fallback
        constant instead of a real spread.
        """
        strat = self._condor()
        full = [{"bid": l["price"] * 0.99, "ask": l["price"] * 1.01}
                for l in strat["legs"]]
        self.assertEqual(ostrat.execution_cost(strat, full)["source"], "measured")

        for broken in (full[:2],                                  # too few
                       full[:3] + [{"bid": 0, "ask": 0}],         # one unusable
                       full[:3] + [{"bid": 5.0, "ask": 5.0}]):    # zero width
            with self.subTest(case=len(broken)):
                self.assertEqual(
                    ostrat.execution_cost(strat, broken)["source"], "estimate")

    def test_cost_falls_on_both_sides_of_reward_risk(self):
        """
        We receive less than the theoretical credit and pay more than the
        theoretical loss. Haircutting only the credit understates the damage.
        """
        strat = self._condor()
        m = ostrat.net_of_cost_metrics(strat)
        c = m["exec_cost_per_share"]
        self.assertAlmostEqual(m["max_profit_after_costs"], strat["max_profit"] - c, places=3)
        self.assertAlmostEqual(m["max_loss_after_costs"],   strat["max_loss"] + c, places=3)
        self.assertLess(m["reward_risk_net"], strat["reward_risk"])

    def test_marginal_structures_fail_once_costs_are_counted(self):
        """
        The finding that motivated this: a 45-DTE 16-delta iron condor clears
        the 0.25 reward/risk bar on mid prices by a hair (0.28) and fails it
        once the round-trip spread is charged. The bar is NOT lowered to
        compensate — CLAUDE.md's tighten-only rail forbids dropping a
        documented floor to book more trades.
        """
        strat = self._condor()
        self.assertGreaterEqual(strat["reward_risk"], 0.25, "passes on mids")
        ok, why = ostrat.passes_risk_filters(strat, account_equity=91000.0)
        self.assertFalse(ok, "must fail once execution cost is charged")
        self.assertIn("net of costs", why)

    def test_tight_real_quotes_are_what_unlock_the_trade(self):
        """
        The fallback is deliberately pessimistic (a 10%-of-mid spread, the
        widest we would knowingly accept). Real quotes on a liquid contract are
        far tighter, and the same structure passes — so the incentive is to
        fetch quotes, not to weaken the gate.
        """
        strat = self._condor()
        tight = [{"bid": l["price"] * 0.99, "ask": l["price"] * 1.01}
                 for l in strat["legs"]]
        ok, why = ostrat.passes_risk_filters(strat, account_equity=91000.0,
                                            leg_quotes=tight)
        self.assertTrue(ok, why)
        self.assertIn("measured", why)

    def test_illiquid_legs_are_rejected_when_quotes_are_real(self):
        """
        options_screener.check_option_liquidity applied a 15%-of-mid spread
        ceiling but was never called from any pipeline, so no option order was
        ever liquidity-checked.
        """
        strat = self._condor()
        wide = [{"bid": l["price"] * 0.70, "ask": l["price"] * 1.30}
                for l in strat["legs"]]
        ok, why = ostrat.passes_risk_filters(strat, account_equity=91000.0,
                                            leg_quotes=wide)
        self.assertFalse(ok)
        self.assertIn("spread", why.lower())


class TestOptionsNoArbitrageValidation(unittest.TestCase):
    """
    Wang et al. §4.3 found no-arbitrage violations in 296 of 356 consolidated
    DJI option markets built from real closing quotes — violations are the norm.
    We are a price taker and cannot harvest them, but the same bounds are a free
    correctness check on our own arithmetic: a credit larger than the width
    securing it is not a great fill, it is a corrupt input.
    """

    def test_valid_structures_pass(self):
        for builder in (ostrat.build_iron_condor, ostrat.build_bull_put_spread,
                        ostrat.build_bear_call_spread, ostrat.build_butterfly_spread,
                        ostrat.build_long_straddle):
            strat = builder(S=150.0, T=45 / 365, sigma=0.25)
            with self.subTest(strategy=strat["strategy"]):
                ok, why = ostrat.validate_no_arbitrage(strat)
                self.assertTrue(ok, f"{strat['strategy']}: {why}")

    def test_credit_may_not_reach_the_width_that_secures_it(self):
        """A vertical whose credit equals its width is riskless profit."""
        strat = ostrat.build_bull_put_spread(S=150.0, T=45 / 365, sigma=0.25)
        width = abs(strat["legs"][0]["strike"] - strat["legs"][1]["strike"])
        strat["net_credit"] = width
        ok, why = ostrat.validate_no_arbitrage(strat)
        self.assertFalse(ok)
        self.assertIn("width", why)

    def test_condor_credit_is_bound_by_the_narrower_wing(self):
        """Only one side can finish in the money, so the narrow wing binds."""
        strat = ostrat.build_iron_condor(S=150.0, T=45 / 365, sigma=0.25)
        by_type = {l["type"]: l["strike"] for l in strat["legs"]}
        narrow = min(by_type["short_put"] - by_type["long_put"],
                     by_type["long_call"] - by_type["short_call"])
        strat["net_credit"] = narrow + 0.01
        ok, why = ostrat.validate_no_arbitrage(strat)
        self.assertFalse(ok)
        self.assertIn("wing width", why)

    def test_out_of_order_strikes_are_rejected(self):
        strat = ostrat.build_iron_condor(S=150.0, T=45 / 365, sigma=0.25)
        for leg in strat["legs"]:
            if leg["type"] == "long_put":
                leg["strike"] = 999.0          # wing inside the short strikes
        ok, why = ostrat.validate_no_arbitrage(strat)
        self.assertFalse(ok)
        self.assertIn("order", why.lower())

    def test_dominated_leg_priced_above_dominant_leg_is_rejected(self):
        """
        Portfolio dominance (Wang et al. Definition 1): the short leg of a put
        credit spread dominates the further-OTM long leg, so it cannot be the
        cheaper of the two.
        """
        strat = ostrat.build_bull_put_spread(S=150.0, T=45 / 365, sigma=0.25)
        by_type = {l["type"]: l for l in strat["legs"]}
        by_type["short_put"]["price"], by_type["long_put"]["price"] = (
            by_type["long_put"]["price"], by_type["short_put"]["price"])
        ok, why = ostrat.validate_no_arbitrage(strat)
        self.assertFalse(ok)
        self.assertIn("dominance", why.lower())

    def test_negative_prices_and_strikes_are_rejected(self):
        for field, value in (("strike", -10.0), ("strike", 0.0), ("price", -1.0)):
            strat = ostrat.build_iron_condor(S=150.0, T=45 / 365, sigma=0.25)
            strat["legs"][0][field] = value
            with self.subTest(field=field, value=value):
                self.assertFalse(ostrat.validate_no_arbitrage(strat)[0])

    def test_validation_runs_before_every_other_gate(self):
        """
        A structurally corrupt strategy must be rejected as corrupt, not pass
        into max-loss and reward/risk arithmetic derived from the bad input.
        """
        strat = ostrat.build_bull_put_spread(S=150.0, T=45 / 365, sigma=0.25)
        strat["net_credit"] = 0.0            # impossible for a credit spread
        ok, why = ostrat.passes_risk_filters(strat, account_equity=91000.0)
        self.assertFalse(ok)
        self.assertIn("no-arbitrage", why.lower())


class TestOptionsShortVolConcentration(unittest.TestCase):
    """
    Aurum, "Arbitrage hedge fund primer" (Jan 2025): short volatility delivers
    "small, consistent gains interrupted by occasional large losses", and
    "increased correlations during market stress" mean separately-sized short-vol
    positions become one position in a spike. February 2018 'Volmageddon' took
    short-vol ETPs down up to 90% in a single day.

    run_options.MAX_POSITIONS was 5 and each trade could risk 2% of equity, so
    the book could hold 10% of equity in max loss, all of it short premium, with
    no portfolio-level check at all.
    """

    def _condor(self, symbol="AAPL"):
        strat = ostrat.build_iron_condor(S=150.0, T=45 / 365, sigma=0.25)
        strat["symbol"] = symbol
        strat["underlying"] = 150.0
        return strat

    def test_long_premium_is_not_capped(self):
        """Buying premium is the other side of this risk, not more of it."""
        straddle = ostrat.build_long_straddle(S=150.0, T=45 / 365, sigma=0.15)
        straddle["symbol"] = "AAPL"
        self.assertFalse(ostrat.is_short_premium(straddle))
        ok, _ = ostrat.aggregate_short_vol_check(straddle, [], 91000.0)
        self.assertTrue(ok)

    def test_concurrent_short_vol_slots_are_capped(self):
        book = [{"symbol": s, "max_loss_per_contract": 300.0, "qty": 1}
                for s in ("MSFT", "GOOGL", "AMZN")]
        ok, why = ostrat.aggregate_short_vol_check(
            self._condor(), book, 1_000_000.0)
        self.assertFalse(ok, "3 open short-vol positions is the cap")
        self.assertIn("spike", why.lower())

    def test_aggregate_risk_cap_binds_before_the_slot_cap(self):
        """
        Two positions is under the slot cap, but at 6% of equity the dollar
        risk is what stops the third — which is the point: five 2% trades were
        never bounded to 6%.
        """
        equity = 91000.0
        cap = equity * ostrat.MAX_AGGREGATE_SHORT_VOL_PCT
        book = [{"symbol": "MSFT", "max_loss_per_contract": cap * 0.6, "qty": 1},
                {"symbol": "GOOGL", "max_loss_per_contract": cap * 0.5, "qty": 1}]
        ok, why = ostrat.aggregate_short_vol_check(self._condor(), book, equity)
        self.assertFalse(ok)
        self.assertIn("aggregate", why.lower())

    def test_quantity_is_counted_not_just_position_count(self):
        """Ten contracts of one spread is ten times the risk of one."""
        equity = 91000.0
        cap = equity * ostrat.MAX_AGGREGATE_SHORT_VOL_PCT
        one = [{"symbol": "MSFT", "max_loss_per_contract": cap / 10, "qty": 1}]
        many = [{"symbol": "MSFT", "max_loss_per_contract": cap / 10, "qty": 10}]
        self.assertTrue(ostrat.aggregate_short_vol_check(
            self._condor(), one, equity)[0])
        self.assertFalse(ostrat.aggregate_short_vol_check(
            self._condor(), many, equity)[0])

    def test_correlated_underlyings_count_as_one_bet(self):
        """
        CLAUDE.md already treats SPY and QQQ as one position for equities.
        Selling premium on both in the same week is one trade sized twice.
        """
        self.assertEqual(ostrat.underlying_group("SPY"),
                         ostrat.underlying_group("QQQ"))
        self.assertNotEqual(ostrat.underlying_group("AAPL"),
                            ostrat.underlying_group("SPY"))
        book = [{"symbol": "SPY", "max_loss_per_contract": 100.0, "qty": 1}]
        ok, why = ostrat.aggregate_short_vol_check(
            self._condor("QQQ"), book, 1_000_000.0)
        self.assertFalse(ok)
        self.assertIn("correlation group", why.lower())

    def test_unreadable_open_risk_refuses_rather_than_counting_zero(self):
        """
        An open short-vol position whose max loss we cannot read is not assumed
        to be riskless. That assumption is how the aggregate cap would be
        silently under-counted.
        """
        book = [{"symbol": "MSFT", "max_loss_per_contract": None, "qty": 1}]
        ok, why = ostrat.aggregate_short_vol_check(self._condor(), book, 91000.0)
        self.assertFalse(ok)
        self.assertIn("unknown max loss", why.lower())

    def test_unknown_equity_is_refused_not_guessed(self):
        for bad in (None, 0, -1):
            with self.subTest(equity=bad):
                ok, why = ostrat.aggregate_short_vol_check(self._condor(), [], bad)
                self.assertFalse(ok)
                self.assertIn("equity unknown", why.lower())


class TestOptionsScenarioShortfall(unittest.TestCase):
    """
    Bemporad, Bellucci & Gabbriellini (Quantitative Finance 2014) §4.1.2 offers
    shortfall objectives (eqs 23-24) against the symmetric minimum-variance one:
    variance penalises a better-than-expected outcome as hard as a worse one.
    For a short-premium book the shape of the loss tail is the whole risk.
    """

    def _condor(self):
        strat = ostrat.build_iron_condor(S=150.0, T=45 / 365, sigma=0.25)
        strat["symbol"] = "TEST"
        strat["underlying"] = 150.0
        return strat

    def test_missing_underlying_raises_instead_of_reporting_total_loss(self):
        """
        `underlying` is stamped by select_strategy, not the builders. It
        defaulted to 0, so every simulated price was 0 — below every breakeven —
        and a strategy dict that skipped the selector silently reported nine
        consecutive max-loss scenarios and a 100% losing fraction.
        """
        raw = ostrat.build_iron_condor(S=150.0, T=45 / 365, sigma=0.25)
        self.assertNotIn("underlying", raw)
        with self.assertRaises(ValueError):
            ostrat.scenario_analysis(raw)

    def test_shortfall_averages_only_the_losing_scenarios(self):
        sa = ostrat.scenario_analysis(self._condor())
        losing = [s["pl"] for s in sa["scenarios"] if s["pl"] < 0]
        self.assertEqual(sa["losing_scenario_count"], len(losing))
        self.assertAlmostEqual(sa["avg_shortfall_per_contract"],
                               sum(losing) / len(losing), places=2)
        self.assertAlmostEqual(sa["max_shortfall_per_contract"], min(losing), places=2)

    def test_shortfall_exposes_the_short_vol_payoff_asymmetry(self):
        """
        The 'pennies in front of a steamroller' shape: a premium seller's
        average loss dwarfs its best case. worst_case alone is one point on the
        curve and flat_case is the scenario a seller most wants to look at.
        """
        sa = ostrat.scenario_analysis(self._condor())
        self.assertLess(abs(sa["best_case"]["pl"]),
                        abs(sa["avg_shortfall_per_contract"]))

    def test_no_losing_scenario_reports_zero_not_a_crash(self):
        sa = ostrat.scenario_analysis(self._condor(), stock_moves=[0.0])
        self.assertEqual(sa["losing_scenario_count"], 0)
        self.assertEqual(sa["avg_shortfall_per_contract"], 0.0)


class TestOptionsStrategySelection(unittest.TestCase):

    def _screen(self, ivr, trend="neutral"):
        # Mirrors the dict options_screener.py emits (IV values as percents).
        return {"symbol": "TEST", "price": 100.0, "hv30": 25.0,
                "current_iv": 30.0, "iv_rank": ivr, "iv_pct": ivr,
                "trend": trend, "expiry_target": "2026-10-16", "dte": 40}

    def test_premium_is_sold_only_when_iv_is_rich(self):
        self.assertIsNotNone(ostrat.select_strategy(self._screen(65)))

    def test_neutral_iv_zone_is_skipped(self):
        """IVR 21-59 has no edge in either direction — take no position."""
        for ivr in (25, 40, 55):
            with self.subTest(ivr=ivr):
                self.assertIsNone(ostrat.select_strategy(self._screen(ivr)))

    def test_premium_is_bought_only_when_iv_is_compressed(self):
        self.assertIsNotNone(ostrat.select_strategy(self._screen(15)))

    def test_degenerate_inputs_are_refused(self):
        for bad in ({"dte": 0}, {"current_iv": 0}, {"price": 0}):
            with self.subTest(bad=bad):
                self.assertIsNone(
                    ostrat.select_strategy(dict(self._screen(65), **bad)))


class TestOptionsManagementRules(unittest.TestCase):
    """Published premium-selling management: 50% profit, exit by 21 DTE."""

    def test_profit_target_is_fifty_percent(self):
        self.assertAlmostEqual(omon.PROFIT_TARGET, 0.50)

    def test_gamma_zone_thresholds(self):
        self.assertEqual(omon.DTE_WARN, 21)
        self.assertEqual(omon.DTE_FORCE_CLOSE, 7)
        self.assertLess(omon.DTE_FORCE_CLOSE, omon.DTE_WARN)

    def test_loss_stop_never_exceeds_credit_collected(self):
        self.assertLessEqual(omon.LOSS_STOP, 1.00)

    def test_defined_risk_structures_report_a_finite_max_loss(self):
        for builder in (ostrat.build_iron_condor, ostrat.build_bull_put_spread,
                        ostrat.build_bear_call_spread):
            with self.subTest(builder=builder.__name__):
                strat = builder(S=100.0, T=40 / 365, sigma=0.30)
                self.assertIsNotNone(strat["max_loss"])
                self.assertGreater(strat["max_loss"], 0)


class TestDeltaStrikeSelection(unittest.TestCase):
    """
    Strikes were a fixed 10% OTM, which ignores volatility: 10% is ~1.6 SD at
    15% IV and ~0.4 SD at 100% IV, so the same structure carried wildly
    different assignment odds. Delta targeting holds the odds constant instead.
    """

    T = 40 / 365

    def test_target_delta_is_achieved_across_volatility_regimes(self):
        from options_pricing import bsm_greeks
        for iv in (0.15, 0.30, 0.60, 1.00):
            for kind in ("call", "put"):
                with self.subTest(iv=iv, type=kind):
                    K = ostrat.strike_for_delta(100.0, self.T, iv,
                                                ostrat.SHORT_STRIKE_DELTA, kind)
                    d = abs(bsm_greeks(100.0, K, self.T,
                                       ostrat.RISK_FREE_RATE, iv, kind)["delta"])
                    self.assertAlmostEqual(d, ostrat.SHORT_STRIKE_DELTA, delta=0.03)

    def test_strikes_widen_as_volatility_rises(self):
        low = ostrat.strike_for_delta(100.0, self.T, 0.15, 0.16, "call")
        high = ostrat.strike_for_delta(100.0, self.T, 1.00, 0.16, "call")
        self.assertGreater(high, low, "a 16-delta call must sit further out at high IV")

    def test_degenerate_inputs_fall_back_rather_than_returning_nonsense(self):
        for sigma, T in ((0.0, self.T), (0.30, 0.0), (-1.0, self.T)):
            with self.subTest(sigma=sigma, T=T):
                K = ostrat.strike_for_delta(100.0, T, sigma, 0.16, "call")
                self.assertGreater(K, 0)
                self.assertLess(K, 1000)

    def test_condor_wings_stay_outside_the_short_strikes(self):
        for iv in (0.15, 0.30, 0.60, 1.00):
            with self.subTest(iv=iv):
                strikes = [l["strike"] for l in
                           ostrat.build_iron_condor(100.0, self.T, iv)["legs"]]
                self.assertEqual(strikes, sorted(strikes),
                                 "legs must be ordered long put < short put < short call < long call")
                self.assertEqual(len(set(strikes)), 4, "no duplicate strikes")

    def test_delta_mode_sells_further_out_than_fixed_offsets_at_high_iv(self):
        """
        At 60% IV a fixed 10% offset sells ~0.30-delta strikes — a higher credit
        bought with roughly double the breach probability. The better headline
        reward/risk is compensation for risk, not a better trade.
        """
        from options_pricing import bsm_greeks
        d = ostrat.build_iron_condor(100.0, self.T, 0.60)
        f = ostrat.build_iron_condor(100.0, self.T, 0.60, use_delta=False)

        def breach(strat):
            legs = {l["type"]: l["strike"] for l in strat["legs"]}
            dp = abs(bsm_greeks(100.0, legs["short_put"], self.T,
                                ostrat.RISK_FREE_RATE, 0.60, "put")["delta"])
            dc = abs(bsm_greeks(100.0, legs["short_call"], self.T,
                                ostrat.RISK_FREE_RATE, 0.60, "call")["delta"])
            return dp + dc

        self.assertLess(breach(d), breach(f) * 0.7,
                        "delta mode must carry materially lower breach probability")

    def test_delta_mode_makes_low_iv_condors_viable(self):
        """A fixed-offset condor at 15% IV collects too little to clear 0.25 R/R."""
        d = ostrat.build_iron_condor(100.0, self.T, 0.15)
        f = ostrat.build_iron_condor(100.0, self.T, 0.15, use_delta=False)
        self.assertGreater(d["reward_risk"], f["reward_risk"])
