#!/usr/bin/env python3
"""
Tests for the core-universe policy and per-symbol track record (v6.1.0).

The policy may only turn an APPROVE into a HOLD. Every test below that touches
enforcement also checks it cannot upgrade or loosen anything.

Run:  python3 -m unittest discover -s tests -v
"""

import copy
import csv
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APCA_API_KEY_ID", "test")
os.environ.setdefault("APCA_API_SECRET_KEY", "test")

import symbol_stats     # noqa: E402
import universe_policy  # noqa: E402


def _write_csv(path, header, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def _cfg(status="approved", core=("AAPL", "MSFT"), hedge=("SQQQ",), probation=("NOK",)):
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "core_universe.json")
        with open(path, "w") as f:
            json.dump({"status": status,
                       "core": {s: {"added": "2026-11-04", "why": "x"} for s in core},
                       "hedge": {s: "x" for s in hedge},
                       "probation": {s: "x" for s in probation}}, f)
        return universe_policy.load_config(path)


def _scored(symbol="AAPL", score=8.2, rec="APPROVE", gate=7.0):
    return {"symbol": symbol, "recommendation": rec, "score": score,
            "approval_gate": gate, "rationale": "r"}


RT_HEADER = ["symbol", "entry_date", "exit_date", "qty", "entry", "exit", "pnl_dollar", "pnl_pct", "hold_days"]
CAL_HEADER = ["date", "symbol", "side", "judge_score", "reviewer_score", "pnl_pct", "verdict", "delta"]


class TestSymbolStats(unittest.TestCase):

    def _stats(self, round_trips, calibration=()):
        with tempfile.TemporaryDirectory() as d:
            _write_csv(os.path.join(d, "round-trips.csv"), RT_HEADER, round_trips)
            _write_csv(os.path.join(d, "calibration-log.csv"), CAL_HEADER, calibration)
            return symbol_stats.load_stats(d)

    def test_win_rate_and_average(self):
        s = self._stats([
            ["AAPL", "d", "2026-07-01", 1, 1, 1, 50, 5.0, 1],
            ["AAPL", "d", "2026-07-02", 1, 1, 1, -20, -2.0, 1],
        ])["AAPL"]
        self.assertEqual(s["trades"], 2)
        self.assertEqual(s["win_rate"], 0.5)
        self.assertEqual(s["avg_pnl_pct"], 1.5)
        self.assertEqual(s["total_pnl_usd"], 30.0)

    def test_quarantined_symbol_has_no_record(self):
        s = self._stats([["LBGJ", "d", "d", 1, 1, 1, 474000, 90.0, 1]])
        self.assertNotIn("LBGJ", s)

    def test_data_suspect_trade_is_not_counted_either_way(self):
        s = self._stats([
            ["XYZ", "d", "d", 1, 1, 1, 1, 2500.0, 1],
            ["XYZ", "d", "d", 1, 1, 1, 1, 3.0, 1],
        ])["XYZ"]
        self.assertEqual(s["trades"], 1)

    def test_options_contract_is_excluded(self):
        s = self._stats([["NVDA261016P00200000", "d", "d", 1, 1, 1, 1, 5.0, 1]])
        self.assertEqual(s, {})

    def test_placeholder_minus_100_calibration_row_is_ignored(self):
        s = self._stats([], [["2026-06-16", "AAPL", "buy", 9.25, 3.0, -100.0, "POOR_TRADE", -6]])
        self.assertNotIn("AAPL", s)

    def test_judge_score_for_winners_vs_losers(self):
        s = self._stats([], [
            ["d1", "NOK", "buy", 9.0, 8, 4.0, "GOOD_TRADE", 0],
            ["d2", "NOK", "buy", 8.0, 4, -6.0, "POOR_TRADE", 0],
        ])["NOK"]
        self.assertEqual(s["avg_judge_winners"], 9.0)
        self.assertEqual(s["avg_judge_losers"], 8.0)
        self.assertEqual(s["avg_judge_score"], 8.5)

    def test_missing_files_give_empty_stats(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(symbol_stats.load_stats(d), {})


class TestRecordGate(unittest.TestCase):

    def test_no_record_raises_the_bar(self):
        self.assertEqual(universe_policy.record_gate(None)[0], 8.0)
        self.assertEqual(universe_policy.record_gate({"trades": 0})[0], 8.0)

    def test_thin_record(self):
        self.assertEqual(universe_policy.record_gate({"trades": 2, "avg_pnl_pct": 9.0})[0], 7.5)

    def test_losing_record_is_penalised(self):
        self.assertEqual(universe_policy.record_gate({"trades": 4, "avg_pnl_pct": -0.8})[0], 8.0)

    def test_established_winning_record_adds_no_penalty(self):
        self.assertEqual(universe_policy.record_gate({"trades": 3, "avg_pnl_pct": 1.0})[0], 0.0)

    def test_gate_never_below_the_judges_own(self):
        cfg = _cfg()
        rec = {"trades": 5, "avg_pnl_pct": 3.0}
        out = universe_policy.apply(_scored("AAPL", 8.6, gate=8.5), "AAPL", rec, cfg)
        self.assertEqual(out["universe_policy"]["required_gate"], 8.5)


class TestEnforcement(unittest.TestCase):

    def test_outside_core_approve_becomes_hold(self):
        out = universe_policy.apply(_scored("PLTR", 9.5, gate=8.5), "PLTR", None, _cfg())
        self.assertEqual(out["recommendation"], "HOLD")
        self.assertIn("research-only", out["rationale"])

    def test_probation_name_is_research_only_too(self):
        out = universe_policy.apply(_scored("NOK", 9.9), "NOK", None, _cfg())
        self.assertEqual(out["recommendation"], "HOLD")

    def test_core_name_with_no_record_needs_eight(self):
        cfg = _cfg()
        out = universe_policy.apply(_scored("AAPL", 7.6), "AAPL", None, cfg)
        self.assertEqual(out["recommendation"], "HOLD")
        out = universe_policy.apply(_scored("AAPL", 8.0), "AAPL", None, cfg)
        self.assertEqual(out["recommendation"], "APPROVE")

    def test_core_name_with_earned_record_keeps_the_normal_gate(self):
        rec = {"trades": 4, "avg_pnl_pct": 2.0}
        out = universe_policy.apply(_scored("AAPL", 7.1), "AAPL", rec, _cfg())
        self.assertEqual(out["recommendation"], "APPROVE")

    def test_hedge_instruments_are_exempt(self):
        out = universe_policy.apply(_scored("SQQQ", 7.1), "SQQQ", None, _cfg())
        self.assertEqual(out["recommendation"], "APPROVE")
        self.assertIsNone(out["universe_policy"]["would_change"])

    def test_never_upgrades_a_hold_or_veto(self):
        for rec in ("HOLD", "VETO"):
            out = universe_policy.apply(_scored("AAPL", 9.9, rec=rec), "AAPL",
                                        {"trades": 9, "avg_pnl_pct": 5.0}, _cfg())
            self.assertEqual(out["recommendation"], rec)

    def test_never_lowers_a_gate(self):
        for sym in ("AAPL", "PLTR", "SQQQ"):
            for rec in (None, {"trades": 1, "avg_pnl_pct": 1}, {"trades": 9, "avg_pnl_pct": 9}):
                out = universe_policy.apply(_scored(sym, 7.0, gate=7.0), sym, rec, _cfg())
                self.assertGreaterEqual(out["approval_gate"], 7.0)

    def test_shadow_mode_records_but_changes_nothing(self):
        cfg = _cfg(status="proposed")
        before = _scored("PLTR", 9.5, gate=8.5)
        out = universe_policy.apply(copy.deepcopy(before), "PLTR", None, cfg)
        self.assertEqual(out["recommendation"], "APPROVE")
        self.assertEqual(out["approval_gate"], 8.5)
        self.assertFalse(out["universe_policy"]["enforced"])
        self.assertIn("research-only", out["universe_policy"]["would_change"])

    def test_no_config_is_a_no_op(self):
        before = _scored("PLTR", 9.5)
        self.assertEqual(universe_policy.apply(copy.deepcopy(before), "PLTR", None, None), before)


class TestConfigValidation(unittest.TestCase):

    def _load(self, payload):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "c.json")
            with open(path, "w") as f:
                json.dump(payload, f)
            return universe_policy.load_config(path)

    def test_missing_file_means_feature_off(self):
        self.assertIsNone(universe_policy.load_config("/nonexistent/core_universe.json"))

    def test_bad_status_fails_loudly(self):
        with self.assertRaises(ValueError):
            self._load({"status": "maybe", "core": {"AAPL": ""}})

    def test_empty_core_fails_loudly(self):
        with self.assertRaises(ValueError):
            self._load({"status": "approved", "core": {}})

    def test_symbol_in_two_buckets_fails_loudly(self):
        with self.assertRaises(ValueError):
            self._load({"status": "approved", "core": {"AAPL": ""}, "probation": {"AAPL": ""}})

    def test_lowercase_symbol_fails_loudly(self):
        with self.assertRaises(ValueError):
            self._load({"status": "approved", "core": {"aapl": ""}})

    def test_shipped_config_is_valid_and_enforced(self):
        cfg = universe_policy.load_config()
        self.assertIsNotNone(cfg)
        self.assertEqual(cfg["status"], "approved")
        self.assertTrue(cfg["enforced"])
        self.assertGreaterEqual(len(cfg["core"]), 10)
        self.assertTrue(cfg["speculative"] <= cfg["core"])
        self.assertEqual({"MSTR", "RKLB", "ASTS", "BBAI"}, cfg["speculative"])

    def test_every_core_name_is_on_the_watchlist(self):
        """A core name missing from watchlist.json is only researched if a
        discovery source happens to surface it, and then as a discovery name."""
        cfg = universe_policy.load_config()
        with open(os.path.join(os.path.dirname(universe_policy.__file__), "watchlist.json")) as f:
            wl = {w["symbol"] for w in json.load(f)["watchlist"]}
        self.assertEqual(sorted(cfg["core"] - wl), [])

    def test_core_entry_without_added_date_fails_loudly(self):
        with self.assertRaises(ValueError):
            self._load({"status": "approved", "core": {"AAPL": {"why": "x"}}})

    def test_core_entry_must_be_an_object(self):
        with self.assertRaises(ValueError):
            self._load({"status": "approved", "core": {"AAPL": "x"}})

    def test_speculative_flag_must_be_boolean(self):
        with self.assertRaises(ValueError):
            self._load({"status": "approved",
                        "core": {"AAPL": {"added": "2026-11-04", "speculative": "yes"}}})


class TestSpeculativeSleeve(unittest.TestCase):

    def _path(self, status, tmp):
        path = os.path.join(tmp, "c.json")
        with open(path, "w") as f:
            json.dump({"status": status, "core": {
                "AAPL": {"added": "2026-11-04"},
                "RKLB": {"added": "2026-11-04", "speculative": True}}}, f)
        return path

    def test_sleeve_applies_when_enforced(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(universe_policy.speculative_symbols(self._path("approved", d)), {"RKLB"})

    def test_sleeve_is_inert_in_shadow_mode(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(universe_policy.speculative_symbols(self._path("proposed", d)), set())

    def test_no_config_means_no_sleeve(self):
        self.assertEqual(universe_policy.speculative_symbols("/nonexistent.json"), set())

    def test_sleeve_name_counts_toward_the_speculative_position_cap(self):
        """RKLB/ASTS/BBAI/MSTR are not discovery names and are mostly above the
        $500M small-cap line, so without the sleeve they dodge the cap of 2."""
        import judge
        research = {"positions": [{"symbol": "RKLB"}, {"symbol": "ASTS"}],
                    "symbols": {"RKLB": {"source": "watchlist", "fundamentals": {"market_cap_b": 20}},
                                "ASTS": {"source": "watchlist", "fundamentals": {"market_cap_b": 15}}}}
        with mock.patch.object(universe_policy, "speculative_symbols", return_value={"RKLB", "ASTS"}):
            self.assertEqual(judge.count_speculative_positions(research), 2)
        with mock.patch.object(universe_policy, "speculative_symbols", return_value=set()):
            self.assertEqual(judge.count_speculative_positions(research), 0)


if __name__ == "__main__":
    unittest.main()
