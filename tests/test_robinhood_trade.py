#!/usr/bin/env python3
"""
Tests for robinhood_trade.py — guardrails for the real-money book.

Two properties matter most here, repeatedly tested:
1. Nothing in this module ever reads or writes trade.py's
   logs/paper/agent-state.json — every state-touching call must receive this
   book's own state explicitly.
2. logs_dir has no default. An unset logs_dir must raise, never silently
   fall back to a path inside the public repo.
"""

import os
import tempfile
import unittest
from unittest import mock

import robinhood_trade as rt
import trade


def _account(cash=100_000.0, buying_power=100_000.0, portfolio_value=100_000.0):
    return {"cash": cash, "buying_power": buying_power, "portfolio_value": portfolio_value}


def _judge_row(symbol="AAPL", signal="STRONG_BUY", judge_score=9.0, limit_price=150.0):
    return {"symbol": symbol, "signal": signal, "judge_score": judge_score, "limit_price": limit_price}


class TestLogsDirRequired(unittest.TestCase):
    def test_load_real_state_requires_logs_dir(self):
        with self.assertRaises(rt.RealLogsDirNotSet):
            rt.load_real_state(None)
        with self.assertRaises(rt.RealLogsDirNotSet):
            rt.load_real_state("")

    def test_save_real_state_requires_logs_dir(self):
        with self.assertRaises(rt.RealLogsDirNotSet):
            rt.save_real_state(None, {})

    def test_append_fill_requires_logs_dir(self):
        with self.assertRaises(rt.RealLogsDirNotSet):
            rt.append_fill(None, {})


class TestStateIsolationFromPaperBook(unittest.TestCase):
    """honest_equity/max_shares_for must never consult trade.py's own state,
    unlike trade.honest_portfolio_value/calculate_max_shares which do."""

    def test_honest_equity_ignores_trade_py_load_state(self):
        account = _account(portfolio_value=100_000.0)
        positions = []
        real_state = {"entries": {}, "stop_outs": {}}
        with mock.patch.object(trade, "load_state", side_effect=AssertionError(
                "must not call trade.load_state() -- that's the paper book's state")):
            value = rt.honest_equity(account, positions, real_state)
        self.assertEqual(value, 100_000.0)

    def test_max_shares_for_ignores_trade_py_load_state(self):
        account = _account(cash=100_000.0, buying_power=100_000.0, portfolio_value=100_000.0)
        positions = []
        real_state = {"entries": {}, "stop_outs": {}}
        with mock.patch.object(trade, "load_state", side_effect=AssertionError(
                "must not call trade.load_state() -- that's the paper book's state")):
            shares, note = rt.max_shares_for(account, positions, real_state, 100.0, 0.05)
        # 5% of 100k = 5000, floor(5000/100) = 50
        self.assertEqual(shares, 50)


class TestStateRoundTrip(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_load_save_round_trip(self):
        state = rt.load_real_state(self.tmp)
        self.assertEqual(state, {"entries": {}, "stop_outs": {}})
        rt.record_entry(state, "AAPL", 9.0, 10)
        rt.save_real_state(self.tmp, state)
        reloaded = rt.load_real_state(self.tmp)
        self.assertIn("AAPL", reloaded["entries"])
        self.assertEqual(reloaded["entries"]["AAPL"]["qty"], 10)

    def test_record_stop_out_starts_cooldown(self):
        state = rt.load_real_state(self.tmp)
        rt.record_stop_out(state, "WAB")
        remaining = trade.cooldown_remaining_hours("WAB", state)
        self.assertGreater(remaining, 47.0)
        self.assertLessEqual(remaining, 48.0)


class TestBuildBuyCandidates(unittest.TestCase):
    def setUp(self):
        self.state = {"entries": {}, "stop_outs": {}}
        self.watchlist = {}

    def test_strong_buy_with_room_produces_sized_candidate(self):
        account = _account(portfolio_value=100_000.0)
        candidates = rt.build_buy_candidates(
            [_judge_row(judge_score=9.0, limit_price=150.0)],
            account, [], self.state, self.watchlist)
        self.assertEqual(len(candidates), 1)
        c = candidates[0]
        self.assertEqual(c["action"], "BUY")
        self.assertEqual(c["symbol"], "AAPL")
        self.assertGreater(c["qty"], 0)
        self.assertEqual(c["tier"], "TIER1")

    def test_already_held_symbol_is_skipped_not_averaged_down(self):
        account = _account()
        positions = [{"symbol": "AAPL", "qty": 10, "avg_entry_price": 140.0,
                      "current_price": 150.0, "unrealized_plpc": 0.07}]
        candidates = rt.build_buy_candidates(
            [_judge_row()], account, positions, self.state, self.watchlist)
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("already held" in r for r in candidates[0]["reasons"]))

    def test_cooldown_blocks_reentry(self):
        rt.record_stop_out(self.state, "AAPL")
        account = _account()
        candidates = rt.build_buy_candidates(
            [_judge_row()], account, [], self.state, self.watchlist)
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("cooldown" in r for r in candidates[0]["reasons"]))

    def test_weak_signal_in_risk_off_is_skipped(self):
        account = _account()
        candidates = rt.build_buy_candidates(
            [_judge_row(signal="WEAK_BUY")], account, [], self.state, self.watchlist,
            macro_regime="RISK_OFF")
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("RISK_OFF" in r for r in candidates[0]["reasons"]))

    def test_strong_buy_survives_risk_off(self):
        account = _account()
        candidates = rt.build_buy_candidates(
            [_judge_row(signal="STRONG_BUY")], account, [], self.state, self.watchlist,
            macro_regime="RISK_OFF")
        self.assertEqual(candidates[0]["action"], "BUY")
        # Dalio ceiling should show up in the sizing notes
        self.assertTrue(any("RISK-OFF" in n for n in candidates[0]["sizing_notes"]))

    def test_insufficient_cash_is_skipped(self):
        account = _account(cash=1000.0, buying_power=1000.0, portfolio_value=100_000.0)
        candidates = rt.build_buy_candidates(
            [_judge_row(limit_price=150.0)], account, [], self.state, self.watchlist)
        self.assertEqual(candidates[0]["action"], "SKIP")

    def test_below_min_price_is_skipped(self):
        account = _account()
        candidates = rt.build_buy_candidates(
            [_judge_row(limit_price=2.0)], account, [], self.state, self.watchlist)
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("floor" in r for r in candidates[0]["reasons"]))

    def test_watchlist_signal_only_is_skipped(self):
        account = _account()
        watchlist = {"VIXY": {"type": "signal_only"}}
        candidates = rt.build_buy_candidates(
            [_judge_row(symbol="VIXY", limit_price=20.0)], account, [], self.state, watchlist)
        self.assertEqual(candidates[0]["action"], "SKIP")

    def test_pending_buy_order_blocks_a_second_one(self):
        """The v4.0.0 scheduling gap: a BUY order awaiting Trade Approval
        isn't a position yet, so a re-run 30 minutes later must not queue
        a second order for the same symbol."""
        account = _account()
        open_orders = [{"symbol": "AAPL", "side": "buy", "status": "pending_new"}]
        candidates = rt.build_buy_candidates(
            [_judge_row()], account, [], self.state, self.watchlist, open_orders=open_orders)
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("pending buy order" in r for r in candidates[0]["reasons"]))

    def test_filled_or_cancelled_order_does_not_block(self):
        account = _account()
        for status in ("filled", "cancelled", "rejected", "expired"):
            with self.subTest(status=status):
                open_orders = [{"symbol": "AAPL", "side": "buy", "status": status}]
                candidates = rt.build_buy_candidates(
                    [_judge_row()], account, [], self.state, self.watchlist,
                    open_orders=open_orders)
                self.assertEqual(candidates[0]["action"], "BUY")

    def test_pending_sell_order_does_not_block_a_buy(self):
        account = _account()
        open_orders = [{"symbol": "AAPL", "side": "sell", "status": "pending_new"}]
        candidates = rt.build_buy_candidates(
            [_judge_row()], account, [], self.state, self.watchlist, open_orders=open_orders)
        self.assertEqual(candidates[0]["action"], "BUY")

    def test_pending_buy_on_a_different_symbol_does_not_block(self):
        account = _account()
        open_orders = [{"symbol": "MSFT", "side": "buy", "status": "pending_new"}]
        candidates = rt.build_buy_candidates(
            [_judge_row(symbol="AAPL")], account, [], self.state, self.watchlist,
            open_orders=open_orders)
        self.assertEqual(candidates[0]["action"], "BUY")

    def test_no_open_orders_passed_defaults_safely(self):
        """open_orders must default to safe (no false blocks), not crash."""
        account = _account()
        candidates = rt.build_buy_candidates(
            [_judge_row()], account, [], self.state, self.watchlist)
        self.assertEqual(candidates[0]["action"], "BUY")


class TestReconcileFills(unittest.TestCase):
    """v4.1.0's other finding: a BUY placed without chat confirmation can
    fill on a tick after the one that placed it. Without reconciliation,
    record_entry() (cooldown/min-hold tracking) silently never runs for it."""

    def setUp(self):
        self.state = {}

    def test_record_pending_order_stores_judge_score_and_qty(self):
        rt.record_pending_order(self.state, "AAPL", judge_score=9.0, qty=10,
                                 order_id="order-1")
        self.assertIn("AAPL", self.state["pending"])
        self.assertEqual(self.state["pending"]["AAPL"]["judge_score"], 9.0)
        self.assertEqual(self.state["pending"]["AAPL"]["qty"], 10)
        self.assertEqual(self.state["pending"]["AAPL"]["order_id"], "order-1")

    def test_filled_pending_order_promotes_to_entries(self):
        rt.record_pending_order(self.state, "AAPL", judge_score=9.0, qty=10,
                                 order_id="order-1")
        positions = [{"symbol": "AAPL", "qty": 10, "avg_entry_price": 150.25,
                      "current_price": 151.0, "unrealized_plpc": 0.005}]
        result = rt.reconcile_fills(positions, open_orders=[], state=self.state)

        self.assertEqual(len(result["filled"]), 1)
        self.assertEqual(result["filled"][0]["symbol"], "AAPL")
        self.assertEqual(result["filled"][0]["filled_price"], 150.25)
        self.assertEqual(result["filled"][0]["judge_score"], 9.0)
        self.assertNotIn("AAPL", self.state["pending"])
        self.assertIn("AAPL", self.state["entries"])
        self.assertEqual(self.state["entries"]["AAPL"]["judge_score"], 9.0)

    def test_still_open_order_is_left_pending(self):
        rt.record_pending_order(self.state, "AAPL", judge_score=9.0, qty=10,
                                 order_id="order-1")
        open_orders = [{"symbol": "AAPL", "side": "buy", "status": "pending_new"}]
        result = rt.reconcile_fills([], open_orders=open_orders, state=self.state)

        self.assertEqual(result["filled"], [])
        self.assertEqual(result["expired"], [])
        self.assertIn("AAPL", self.state["pending"])
        self.assertNotIn("AAPL", self.state.get("entries", {}))

    def test_order_resolved_without_filling_is_dropped_not_entered(self):
        rt.record_pending_order(self.state, "AAPL", judge_score=9.0, qty=10,
                                 order_id="order-1")
        result = rt.reconcile_fills([], open_orders=[], state=self.state)

        self.assertEqual(result["filled"], [])
        self.assertEqual(len(result["expired"]), 1)
        self.assertEqual(result["expired"][0]["symbol"], "AAPL")
        self.assertNotIn("AAPL", self.state["pending"])
        self.assertNotIn("AAPL", self.state.get("entries", {}))

    def test_no_pending_orders_is_a_safe_no_op(self):
        result = rt.reconcile_fills([], open_orders=[], state=self.state)
        self.assertEqual(result, {"filled": [], "expired": []})

    def test_multiple_pending_orders_resolve_independently(self):
        rt.record_pending_order(self.state, "AAPL", judge_score=9.0, qty=10,
                                 order_id="order-1")
        rt.record_pending_order(self.state, "MSFT", judge_score=8.0, qty=5,
                                 order_id="order-2")
        positions = [{"symbol": "AAPL", "qty": 10, "avg_entry_price": 150.0,
                      "current_price": 151.0, "unrealized_plpc": 0.006}]
        result = rt.reconcile_fills(positions, open_orders=[], state=self.state)

        self.assertEqual([f["symbol"] for f in result["filled"]], ["AAPL"])
        self.assertEqual([e["symbol"] for e in result["expired"]], ["MSFT"])
        self.assertIn("AAPL", self.state["entries"])
        self.assertNotIn("AAPL", self.state["pending"])
        self.assertNotIn("MSFT", self.state["pending"])


def _position(symbol="AAPL", qty=100, avg_entry_price=100.0, current_price=100.0,
              unrealized_plpc=0.0):
    return {"symbol": symbol, "qty": qty, "avg_entry_price": avg_entry_price,
            "current_price": current_price, "unrealized_plpc": unrealized_plpc}


class TestCheckProfitTargets(unittest.TestCase):
    """Hard Rule 7: T1 at entry + 2x stop distance, T2 at entry + 3x. AAPL is
    TIER1 (20% stop), so stop_distance = $20, T1 = $140, T2 = $160."""

    def setUp(self):
        self.state = {}
        rt.record_entry(self.state, "AAPL", 9.0, 100, entry_price=100.0)

    def test_no_entry_price_on_record_is_skipped_not_guessed(self):
        state = {}
        rt.record_entry(state, "AAPL", 9.0, 100)  # no entry_price
        positions = [_position(current_price=200.0)]
        self.assertEqual(rt.check_profit_targets(positions, state), [])

    def test_below_t1_produces_no_target(self):
        positions = [_position(current_price=139.0)]
        self.assertEqual(rt.check_profit_targets(positions, self.state), [])

    def test_t1_reached_produces_partial_target(self):
        positions = [_position(current_price=141.0)]
        targets = rt.check_profit_targets(positions, self.state)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["symbol"], "AAPL")
        self.assertEqual(targets[0]["target"], "T1")
        self.assertEqual(targets[0]["qty"], 40)  # floor(100 * 0.40)

    def test_t1_already_taken_is_not_reproposed(self):
        self.state["entries"]["AAPL"]["t1_taken"] = True
        positions = [_position(qty=60, current_price=145.0)]
        targets = rt.check_profit_targets(positions, self.state)
        self.assertEqual(targets, [])  # below T2 ($160), and T1 already done

    def test_t2_reached_after_t1_taken_produces_partial_target(self):
        self.state["entries"]["AAPL"]["t1_taken"] = True
        positions = [_position(qty=60, current_price=161.0)]
        targets = rt.check_profit_targets(positions, self.state)
        self.assertEqual(len(targets), 1)
        self.assertEqual(targets[0]["target"], "T2")
        self.assertEqual(targets[0]["qty"], 40)

    def test_t2_not_reproposed_once_taken(self):
        self.state["entries"]["AAPL"]["t1_taken"] = True
        self.state["entries"]["AAPL"]["t2_taken"] = True
        positions = [_position(qty=20, current_price=200.0)]
        self.assertEqual(rt.check_profit_targets(positions, self.state), [])

    def test_sell_qty_never_exceeds_held_qty(self):
        # Original entry was 100 sh, but only 10 remain held somehow -- must
        # never propose selling more than what's actually there.
        positions = [_position(qty=10, current_price=141.0)]
        targets = rt.check_profit_targets(positions, self.state)
        self.assertEqual(targets[0]["qty"], 10)


class TestCheckStopsBreakeven(unittest.TestCase):
    """Once T1 fires, Hard Rule 7 says the stop moves to breakeven on the
    remainder -- tighter than AAPL's normal 20% TIER1 stop."""

    def setUp(self):
        self.state = {}
        rt.record_entry(self.state, "AAPL", 9.0, 100, entry_price=100.0)

    def test_above_breakeven_after_t1_is_not_stopped(self):
        self.state["entries"]["AAPL"]["t1_taken"] = True
        positions = [_position(qty=60, current_price=105.0, unrealized_plpc=0.05)]
        result = rt.check_stops(positions, self.state)
        self.assertEqual(result["stop_losses"], [])

    def test_at_or_below_breakeven_after_t1_stops_even_though_above_tier_stop(self):
        # TIER1 stop is 20% down ($80) -- $99 is nowhere near it, but breakeven
        # after T1 is $100, so this must still trigger.
        self.state["entries"]["AAPL"]["t1_taken"] = True
        positions = [_position(qty=60, current_price=99.0, unrealized_plpc=-0.01)]
        result = rt.check_stops(positions, self.state)
        self.assertEqual(len(result["stop_losses"]), 1)
        self.assertIn("breakeven", result["stop_losses"][0]["reason"])

    def test_without_t1_taken_normal_tier_stop_still_applies(self):
        positions = [_position(qty=100, current_price=99.0, unrealized_plpc=-0.01)]
        result = rt.check_stops(positions, self.state)
        self.assertEqual(result["stop_losses"], [])  # nowhere near -20%

    def test_without_t1_taken_tier_stop_breach_still_fires(self):
        positions = [_position(qty=100, current_price=79.0, unrealized_plpc=-0.21)]
        result = rt.check_stops(positions, self.state)
        self.assertEqual(len(result["stop_losses"]), 1)
        self.assertNotIn("breakeven", result["stop_losses"][0]["reason"])


class TestBuildSellCandidates(unittest.TestCase):
    def setUp(self):
        self.state = {}

    def test_stop_loss_produces_sell_full(self):
        rt.record_entry(self.state, "WAB", 7.0, 15, entry_price=289.07)
        positions = [_position("WAB", qty=15, avg_entry_price=289.07,
                                current_price=250.0, unrealized_plpc=-0.135)]
        candidates = rt.build_sell_candidates(positions, self.state)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["action"], "SELL_FULL")
        self.assertEqual(candidates[0]["qty"], 15)

    def test_profit_target_produces_sell_partial(self):
        rt.record_entry(self.state, "AAPL", 9.0, 100, entry_price=100.0)
        positions = [_position(current_price=141.0)]
        candidates = rt.build_sell_candidates(positions, self.state)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["action"], "SELL_PARTIAL")

    def test_phantom_position_skips_instead_of_selling(self):
        rt.record_entry(self.state, "LBGJ", 5.0, 154903, entry_price=0.0288)
        positions = [_position("LBGJ", qty=154903, avg_entry_price=0.0288,
                                current_price=3.79, unrealized_plpc=131.6)]
        candidates = rt.build_sell_candidates(positions, self.state)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("phantom equity" in r for r in candidates[0]["reasons"]))

    def test_pending_sell_order_blocks_a_second_one(self):
        rt.record_entry(self.state, "WAB", 7.0, 15, entry_price=289.07)
        positions = [_position("WAB", qty=15, avg_entry_price=289.07,
                                current_price=250.0, unrealized_plpc=-0.135)]
        open_orders = [{"symbol": "WAB", "side": "sell", "status": "pending_new"}]
        candidates = rt.build_sell_candidates(positions, self.state, open_orders=open_orders)
        self.assertEqual(candidates[0]["action"], "SKIP")
        self.assertTrue(any("pending sell order" in r for r in candidates[0]["reasons"]))

    def test_no_triggers_produces_no_candidates(self):
        rt.record_entry(self.state, "AAPL", 9.0, 100, entry_price=100.0)
        positions = [_position(current_price=105.0)]
        self.assertEqual(rt.build_sell_candidates(positions, self.state), [])


class TestReconcileSellFills(unittest.TestCase):
    def setUp(self):
        self.state = {}

    def test_full_exit_fills_when_symbol_disappears_from_positions(self):
        rt.record_entry(self.state, "WAB", 7.0, 15, entry_price=289.07)
        rt.record_pending_sell(self.state, "WAB", "SELL_FULL", qty=15, order_id="o1")
        result = rt.reconcile_sell_fills([], open_orders=[], state=self.state)
        self.assertEqual(len(result["filled"]), 1)
        self.assertNotIn("WAB", self.state["entries"])        # cleared
        self.assertIn("WAB", self.state["stop_outs"])          # cooldown started
        self.assertNotIn("WAB", self.state["pending_sells"])

    def test_full_exit_still_open_is_left_pending(self):
        rt.record_entry(self.state, "WAB", 7.0, 15, entry_price=289.07)
        rt.record_pending_sell(self.state, "WAB", "SELL_FULL", qty=15, order_id="o1")
        open_orders = [{"symbol": "WAB", "side": "sell", "status": "pending_new"}]
        positions = [_position("WAB", qty=15, avg_entry_price=289.07, current_price=250.0)]
        result = rt.reconcile_sell_fills(positions, open_orders, self.state)
        self.assertEqual(result, {"filled": [], "expired": []})
        self.assertIn("WAB", self.state["pending_sells"])

    def test_full_exit_expires_without_filling(self):
        rt.record_entry(self.state, "WAB", 7.0, 15, entry_price=289.07)
        rt.record_pending_sell(self.state, "WAB", "SELL_FULL", qty=15, order_id="o1")
        positions = [_position("WAB", qty=15, avg_entry_price=289.07, current_price=250.0)]
        result = rt.reconcile_sell_fills(positions, open_orders=[], state=self.state)
        self.assertEqual(len(result["expired"]), 1)
        self.assertNotIn("WAB", self.state["pending_sells"])
        self.assertIn("WAB", self.state["entries"])  # untouched, position still held

    def test_partial_exit_fills_when_held_qty_drops(self):
        rt.record_entry(self.state, "AAPL", 9.0, 100, entry_price=100.0)
        rt.record_pending_sell(self.state, "AAPL", "SELL_PARTIAL", qty=40,
                                order_id="o2", target="T1", qty_before_order=100)
        positions = [_position(qty=60, current_price=141.0)]
        result = rt.reconcile_sell_fills(positions, open_orders=[], state=self.state)
        self.assertEqual(len(result["filled"]), 1)
        self.assertTrue(self.state["entries"]["AAPL"]["t1_taken"])
        self.assertNotIn("AAPL", self.state["pending_sells"])

    def test_partial_exit_expires_without_filling(self):
        rt.record_entry(self.state, "AAPL", 9.0, 100, entry_price=100.0)
        rt.record_pending_sell(self.state, "AAPL", "SELL_PARTIAL", qty=40,
                                order_id="o2", target="T1", qty_before_order=100)
        positions = [_position(qty=100, current_price=141.0)]  # unchanged qty
        result = rt.reconcile_sell_fills(positions, open_orders=[], state=self.state)
        self.assertEqual(len(result["expired"]), 1)
        self.assertFalse(self.state["entries"]["AAPL"].get("t1_taken"))

    def test_no_pending_sells_is_a_safe_no_op(self):
        result = rt.reconcile_sell_fills([], open_orders=[], state=self.state)
        self.assertEqual(result, {"filled": [], "expired": []})


class TestPortfolioReview(unittest.TestCase):
    """Real-money counterpart to trade.py's TestPortfolioReview -- same
    logic, this book's own state, advisory only (never executes)."""

    def test_clean_position_produces_no_flags(self):
        state = {}
        rt.record_entry(state, "AAPL", 9.0, 100, entry_price=200.0)
        positions = [_position("AAPL", avg_entry_price=200.0, current_price=210.0,
                                unrealized_plpc=0.05)]
        self.assertEqual(rt.portfolio_review(positions, state, {}), [])

    def test_near_tier1_stop_produces_early_warning(self):
        state = {}
        rt.record_entry(state, "AAPL", 9.0, 100, entry_price=200.0)
        positions = [_position("AAPL", avg_entry_price=200.0, unrealized_plpc=-0.18)]
        flags = rt.portfolio_review(positions, state, {})
        self.assertEqual(len(flags), 1)
        self.assertTrue(any("stop" in r for r in flags[0]["reasons"]))

    def test_tier1_thesis_break_with_negative_news_is_flagged(self):
        state = {}
        rt.record_entry(state, "AAPL", 9.0, 100, entry_price=200.0)
        positions = [_position("AAPL", avg_entry_price=200.0, unrealized_plpc=0.02)]
        research = {"symbols": {"AAPL": {
            "technicals": {"ma20": 195.0, "ma50": 205.0},
            "news": [{"headline": "AAPL downgrade on miss and lawsuit", "summary": ""}],
        }}}
        flags = rt.portfolio_review(positions, state, research)
        self.assertEqual(len(flags), 1)
        self.assertTrue(any("thesis-break exception may apply" in r
                             for r in flags[0]["reasons"]))

    def test_tier1_technical_break_without_negative_news_is_not_a_sell_trigger(self):
        state = {}
        rt.record_entry(state, "AAPL", 9.0, 100, entry_price=200.0)
        positions = [_position("AAPL", avg_entry_price=200.0, unrealized_plpc=0.02)]
        research = {"symbols": {"AAPL": {
            "technicals": {"ma20": 195.0, "ma50": 205.0}, "news": [],
        }}}
        flags = rt.portfolio_review(positions, state, research)
        self.assertEqual(len(flags), 1)
        self.assertTrue(any("not a sell trigger" in r for r in flags[0]["reasons"]))

    def test_tier2_symbol_is_never_checked_for_thesis_break(self):
        state = {}
        rt.record_entry(state, "DDOG", 9.0, 50, entry_price=100.0)
        positions = [_position("DDOG", avg_entry_price=100.0, unrealized_plpc=0.02)]
        research = {"symbols": {"DDOG": {
            "technicals": {"ma20": 95.0, "ma50": 105.0},
            "news": [{"headline": "DDOG downgrade on miss", "summary": ""}],
        }}}
        self.assertEqual(rt.portfolio_review(positions, state, research), [])

    def test_missing_entry_record_leaves_days_held_none(self):
        state = {}
        positions = [_position("AAPL", avg_entry_price=200.0, unrealized_plpc=-0.18)]
        flags = rt.portfolio_review(positions, state, {})
        self.assertIsNone(flags[0]["days_held"])


class TestConfirmationPrompt(unittest.TestCase):
    def test_contains_exact_mandatory_phrase(self):
        candidate = {"action": "BUY", "symbol": "AAPL", "qty": 10, "limit_price": 150.0,
                     "judge_score": 9.0, "tier": "TIER1", "stop_price": 120.0}
        prompt = rt.confirmation_prompt(candidate)
        self.assertIn("This is a REAL MONEY trade on Robinhood. Confirm?", prompt)
        self.assertIn("AAPL", prompt)

    def test_sell_full_contains_exact_mandatory_phrase(self):
        candidate = {"action": "SELL_FULL", "symbol": "WAB", "qty": 15,
                     "limit_price": 247.5, "tier": "TIER2", "reason": "STOP-LOSS"}
        prompt = rt.confirmation_prompt(candidate)
        self.assertIn("This is a REAL MONEY trade on Robinhood. Confirm?", prompt)
        self.assertIn("WAB", prompt)
        self.assertIn("SELL (full exit)", prompt)

    def test_sell_partial_contains_target_and_mandatory_phrase(self):
        candidate = {"action": "SELL_PARTIAL", "symbol": "AAPL", "qty": 40,
                     "limit_price": 140.7, "target": "T1", "reason": "Hard Rule 7"}
        prompt = rt.confirmation_prompt(candidate)
        self.assertIn("This is a REAL MONEY trade on Robinhood. Confirm?", prompt)
        self.assertIn("SELL (partial, T1)", prompt)

    def test_refuses_to_render_a_skip_candidate(self):
        with self.assertRaises(ValueError):
            rt.confirmation_prompt({"action": "SKIP", "reasons": ["x"]})


class TestRenderJournalEntry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.account = _account(portfolio_value=100_000.0)
        self.state = {"entries": {}, "stop_outs": {}}

    def test_no_activity_day_still_produces_valid_entry(self):
        md = rt.render_journal_entry("2026-10-28", self.account, [], self.state)
        self.assertIn("# Real-Money Trade Journal — 2026-10-28", md)
        self.assertIn("No trades executed today", md)
        self.assertIn("No open positions", md)
        self.assertIn("None triggered today", md)

    def test_fills_appear_in_trades_table(self):
        fills = [{"time": "10:15", "symbol": "AAPL", "side": "buy", "qty": 10,
                  "filled_price": 150.25, "reason": "Judge APPROVE 9.0"}]
        md = rt.render_journal_entry("2026-10-28", self.account, [], self.state,
                                      fills_today=fills)
        self.assertIn("AAPL", md)
        self.assertIn("BUY", md)
        self.assertIn("$150.25", md)

    def test_stop_loss_and_phantom_and_skipped_sections_render(self):
        stop_losses = [{"symbol": "WAB", "tier": "TIER2", "reason": "STOP-LOSS TIER2 (-12%): down 13.0%"}]
        phantom = [{"symbol": "LBGJ", "action": "TRADING_BLOCKED", "reasons": ["price ratio implausible"]}]
        skipped = [{"symbol": "PFE", "reasons": ["in cooldown for 10.0 more hours"]}]
        md = rt.render_journal_entry("2026-10-28", self.account, [], self.state,
                                      stop_losses=stop_losses, phantom_flags=phantom,
                                      skipped=skipped)
        self.assertIn("Stop-Losses Triggered", md)
        self.assertIn("WAB", md)
        self.assertIn("Phantom Equity Flags", md)
        self.assertIn("LBGJ", md)
        self.assertIn("Candidates Considered, Not Taken", md)
        self.assertIn("PFE", md)

    def test_never_duplicates_market_research_section(self):
        # The real journal must point at the shared research journal, not
        # re-render technicals/news -- that's the public repo's job, and a
        # second copy here is exactly how the two would drift apart.
        md = rt.render_journal_entry("2026-10-28", self.account, [], self.state)
        self.assertNotIn("## Market Research", md)
        self.assertIn("logs/paper/2026-10-28.md", md)

    def test_write_journal_file_requires_logs_dir(self):
        with self.assertRaises(rt.RealLogsDirNotSet):
            rt.write_journal_file(None, "2026-10-28", "content")

    def test_write_journal_file_persists_to_date_named_file(self):
        path = rt.write_journal_file(self.tmp, "2026-10-28", "hello")
        self.assertEqual(path, os.path.join(self.tmp, "2026-10-28.md"))
        with open(path) as f:
            self.assertEqual(f.read(), "hello")


class TestAppendFill(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()

    def test_writes_header_once_then_appends(self):
        fill = {"date": "2026-10-28", "symbol": "AAPL", "side": "buy", "qty": 10,
                "filled_price": 150.0, "order_id": "abc123", "status": "filled"}
        rt.append_fill(self.tmp, fill)
        rt.append_fill(self.tmp, dict(fill, order_id="def456"))
        with open(os.path.join(self.tmp, "trade-log.csv")) as f:
            lines = f.read().splitlines()
        self.assertEqual(lines[0], ",".join(rt.TRADE_LOG_HEADER))
        self.assertEqual(len(lines), 3)  # header + 2 fills


if __name__ == "__main__":
    unittest.main()
