#!/usr/bin/env python3
"""
End-to-end dry run of the Robinhood Execution Procedure using synthetic
data only. No network call, no MCP tool, no real account touched anywhere
in this file -- this proves the pipeline's logic chains together correctly
(state -> stop/phantom checks -> candidate building -> confirmation ->
recording a fill -> journaling -> persisting state), which is the one kind
of "does it work end to end" verification possible without a live Robinhood
session. It is not, and cannot be, a substitute for an actual live test.
"""

import os
import tempfile
import unittest

import robinhood_trade as rt


class TestFullDryRun(unittest.TestCase):
    def setUp(self):
        self.logs_dir = tempfile.mkdtemp()

    def test_full_session_with_a_buy_a_stop_and_a_skip(self):
        # 1. Fresh state, as a brand-new real-money book would start.
        state = rt.load_real_state(self.logs_dir)
        self.assertEqual(state, {"entries": {}, "stop_outs": {}})

        # 2. Synthetic account + positions, as if fetched from Robinhood MCP.
        account = {"cash": 50_000.0, "buying_power": 50_000.0, "portfolio_value": 100_000.0}
        positions = [
            {"symbol": "WAB", "qty": 15, "avg_entry_price": 289.07,
             "current_price": 250.00, "unrealized_plpc": -0.1351},  # breached TIER2 -12% stop
        ]

        # 3. Stop/phantom checks run before any new buy.
        stop_results = rt.check_stops(positions, state)
        self.assertEqual(len(stop_results["stop_losses"]), 1)
        self.assertEqual(stop_results["stop_losses"][0]["symbol"], "WAB")
        phantom_flags = rt.phantom_audit(positions, state)
        self.assertEqual(phantom_flags, [])

        # 4. Record the stop-out (same as trade.py would after actually
        #    closing the position) and confirm it starts a cooldown.
        rt.record_stop_out(state, "WAB")
        self.assertGreater(trade_cooldown(state), 0)

        # 5. Today's Judge APPROVE rows, with limit prices computed per Hard
        #    Rule 2 by the (simulated) interactive session.
        judge_approvals = [
            {"symbol": "AAPL", "signal": "STRONG_BUY", "judge_score": 9.0, "limit_price": 150.0},
            {"symbol": "WAB", "signal": "WEAK_BUY", "judge_score": 7.5, "limit_price": 255.0},  # should be cooldown-skipped
        ]
        watchlist = {}
        candidates = rt.build_buy_candidates(
            judge_approvals, account, positions, state, watchlist,
            macro_regime="NEUTRAL", vix_size_multiplier=1.0)

        buys = [c for c in candidates if c["action"] == "BUY"]
        skips = [c for c in candidates if c["action"] == "SKIP"]
        self.assertEqual(len(buys), 1)
        self.assertEqual(buys[0]["symbol"], "AAPL")
        self.assertEqual(len(skips), 1)
        self.assertEqual(skips[0]["symbol"], "WAB")
        self.assertTrue(any("cooldown" in r for r in skips[0]["reasons"]))

        # 6. The confirmation line that must be shown verbatim before any
        #    MCP order-placement call -- this test cannot place the order,
        #    only prove the line is built correctly from the candidate.
        prompt = rt.confirmation_prompt(buys[0])
        self.assertIn("This is a REAL MONEY trade on Robinhood. Confirm?", prompt)
        self.assertIn("AAPL", prompt)

        # 7. Simulate the user confirming and the MCP tool returning a fill
        #    (this dict shape is what a real place_equity_order result would
        #    need to be mapped into -- no such call happens here).
        fill = {
            "date": "2026-10-28", "symbol": "AAPL", "side": "buy",
            "qty": buys[0]["qty"], "filled_price": buys[0]["limit_price"],
            "order_id": "sim-order-001", "status": "filled",
        }
        rt.record_entry(state, "AAPL", buys[0]["judge_score"], buys[0]["qty"])
        rt.append_fill(self.logs_dir, fill)
        rt.save_real_state(self.logs_dir, state)

        # 8. Journal the day and persist it.
        md = rt.render_journal_entry(
            "2026-10-28", account, positions, state,
            fills_today=[{"time": "10:15", "symbol": "AAPL", "side": "buy",
                          "qty": buys[0]["qty"], "filled_price": buys[0]["limit_price"],
                          "reason": "Judge APPROVE 9.0"}],
            stop_losses=stop_results["stop_losses"],
            skipped=skips,
        )
        journal_path = rt.write_journal_file(self.logs_dir, "2026-10-28", md)

        # 9. Verify everything actually landed on disk, in the right place,
        #    nowhere near the public repo.
        self.assertTrue(self.logs_dir not in os.path.abspath(__file__))  # sanity: not this repo
        with open(os.path.join(self.logs_dir, "trade-log.csv")) as f:
            trade_log = f.read()
        self.assertIn("AAPL", trade_log)
        self.assertIn("sim-order-001", trade_log)

        with open(journal_path) as f:
            journal_text = f.read()
        self.assertIn("AAPL", journal_text)
        self.assertIn("WAB", journal_text)          # stop-loss section
        self.assertIn("cooldown", journal_text.lower())  # skip section

        reloaded_state = rt.load_real_state(self.logs_dir)
        self.assertIn("AAPL", reloaded_state["entries"])
        self.assertIn("WAB", reloaded_state["stop_outs"])


def trade_cooldown(state):
    import trade
    return trade.cooldown_remaining_hours("WAB", state)


if __name__ == "__main__":
    unittest.main()
