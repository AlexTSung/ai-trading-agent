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
