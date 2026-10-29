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


class TestConfirmationPrompt(unittest.TestCase):
    def test_contains_exact_mandatory_phrase(self):
        candidate = {"action": "BUY", "symbol": "AAPL", "qty": 10, "limit_price": 150.0,
                     "judge_score": 9.0, "tier": "TIER1", "stop_price": 120.0}
        prompt = rt.confirmation_prompt(candidate)
        self.assertIn("This is a REAL MONEY trade on Robinhood. Confirm?", prompt)
        self.assertIn("AAPL", prompt)

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
