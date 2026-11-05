#!/usr/bin/env python3
"""
Tests for the weekly core-universe review and the add/cut commands (v6.1.0).

The review only proposes. Nothing it does may edit core_universe.json; only the
explicit add/cut commands do, and those must leave a valid config behind.

Run:  python3 -m unittest discover -s tests -v
"""

import csv
import json
import os
import sys
import tempfile
import unittest
from datetime import date, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APCA_API_KEY_ID", "test")
os.environ.setdefault("APCA_API_SECRET_KEY", "test")

import universe_policy  # noqa: E402
import universe_review as ur  # noqa: E402

TODAY = date(2026, 12, 7)


def _read(path):
    with open(path) as f:
        return f.read()


def _research(signal="STRONG_BUY", cap=50.0, buy=10, sell=1, growth="15.0%", price=100, ma50=95):
    return {"technicals": {"signal": signal, "last_close": price, "ma50": ma50, "rsi": 55, "atr_pct": 2.5},
            "fundamentals": {"market_cap_b": cap, "analyst_buy_count": buy, "analyst_sell_count": sell,
                             "revenue_growth": growth, "is_profitable": True}}


def _history(days, per_day):
    """{date: {"symbols": {...}}} for the last `days` days; per_day(i) -> symbols dict."""
    return {TODAY - timedelta(days=i): {"symbols": per_day(i)} for i in range(days)}


def _entry(added="2026-10-01", speculative=False):
    e = {"added": added, "why": "x"}
    if speculative:
        e["speculative"] = True
    return e


def _eval(sym="AAA", entry=None, research=None, judge=None, stats=None):
    return ur.evaluate_core(sym, entry or _entry(), TODAY, research or {}, judge or {}, stats or {})


class TestEvaluateCore(unittest.TestCase):

    def test_healthy_name_is_kept(self):
        r = _eval(research=_history(8, lambda i: {"AAA": _research()}))
        self.assertFalse(r["suggest_cut"])
        self.assertEqual(r["flags"], [])

    def test_losing_record_is_a_hard_cut_even_inside_the_grace_period(self):
        stats = {"AAA": {"trades": 3, "wins": 0, "win_rate": 0.0, "avg_pnl_pct": -7.0}}
        r = _eval(entry=_entry(added=(TODAY - timedelta(days=2)).isoformat()), stats=stats)
        self.assertTrue(r["in_grace"])
        self.assertTrue(r["suggest_cut"])

    def test_one_soft_flag_does_not_cut_a_normal_name(self):
        r = _eval(research=_history(8, lambda i: {"AAA": _research(signal="STRONG_SELL")}))
        self.assertEqual(len(r["flags"]), 1)
        self.assertFalse(r["suggest_cut"])

    def test_one_soft_flag_cuts_a_speculative_name(self):
        r = _eval(entry=_entry(speculative=True),
                  research=_history(8, lambda i: {"AAA": _research(signal="STRONG_SELL")}))
        self.assertTrue(r["suggest_cut"])

    def test_two_soft_flags_cut_a_normal_name(self):
        r = _eval(research=_history(8, lambda i: {"AAA": _research(signal="STRONG_SELL", buy=1, sell=6)}))
        self.assertGreaterEqual(len(r["flags"]), 2)
        self.assertTrue(r["suggest_cut"])

    def test_soft_flags_are_ignored_for_a_new_name(self):
        r = _eval(entry=_entry(added=(TODAY - timedelta(days=3)).isoformat(), speculative=True),
                  research=_history(8, lambda i: {"AAA": _research(signal="STRONG_SELL")}))
        self.assertTrue(r["flags"])
        self.assertFalse(r["suggest_cut"])

    def test_too_few_sessions_is_not_a_downtrend(self):
        r = _eval(research=_history(3, lambda i: {"AAA": _research(signal="STRONG_SELL")}))
        self.assertEqual(r["flags"], [])

    def test_dormant_needs_enough_judge_history(self):
        few = {TODAY - timedelta(days=i): {"scores": {}} for i in range(3)}
        many = {TODAY - timedelta(days=i): {"scores": {}} for i in range(12)}
        self.assertEqual(_eval(judge=few)["flags"], [])
        self.assertTrue(any("dormant" in f for f in _eval(judge=many)["flags"]))

    def test_name_the_judge_scored_is_not_dormant(self):
        judge = {TODAY - timedelta(days=i): {"scores": {"AAA": {"recommendation": "HOLD", "score": 6}}}
                 for i in range(12)}
        self.assertFalse(any("dormant" in f for f in _eval(judge=judge)["flags"]))

    def test_missing_data_is_never_a_flag(self):
        r = _eval()
        self.assertEqual(r["flags"], [])
        self.assertIsNone(r["metrics"])


class TestSuggestAdditions(unittest.TestCase):
    CFG = {"core": {"AAA": {}}, "hedge": {"SQQQ": ""}, "probation": {"NOK": ""}}

    def _judge(self, sym, days, score=8.6, rec="HOLD", blocked=True):
        s = {"recommendation": rec, "score": score}
        if blocked:
            s["universe_policy"] = {"would_change": "APPROVE -> HOLD (research-only)"}
        return {TODAY - timedelta(days=i): {"scores": {sym: s}} for i in range(days)}

    def _suggest(self, judge, research=None, stats=None):
        research = research if research is not None else _history(3, lambda i: {s: _research() for s in
                                                                             ("PLTR", "NOK", "AAA", "SQQQ")})
        return ur.suggest_additions(self.CFG, TODAY, research, judge, stats or {})

    def test_repeatedly_blocked_strong_name_is_suggested(self):
        out = self._suggest(self._judge("PLTR", 3))
        self.assertEqual([a["symbol"] for a in out], ["PLTR"])
        self.assertEqual(out[0]["approval_days"], 3)

    def test_one_day_is_not_enough(self):
        self.assertEqual(self._suggest(self._judge("PLTR", 1)), [])

    def test_low_score_is_not_enough(self):
        self.assertEqual(self._suggest(self._judge("PLTR", 3, score=7.9)), [])

    def test_unknown_or_small_market_cap_is_skipped_not_guessed(self):
        small = _history(3, lambda i: {"PLTR": _research(cap=0.8)})
        none = _history(3, lambda i: {"PLTR": _research(cap=None)})
        absent = _history(3, lambda i: {})
        for research in (small, none, absent):
            self.assertEqual(self._suggest(self._judge("PLTR", 3), research=research), [])

    def test_losing_record_blocks_the_suggestion(self):
        stats = {"PLTR": {"trades": 2, "avg_pnl_pct": -3.0}}
        self.assertEqual(self._suggest(self._judge("PLTR", 3), stats=stats), [])

    def test_core_and_hedge_names_are_never_suggested(self):
        for sym in ("AAA", "SQQQ"):
            self.assertEqual(self._suggest(self._judge(sym, 3)), [])

    def test_probation_name_is_marked(self):
        out = self._suggest(self._judge("NOK", 3))
        self.assertTrue(out[0]["on_probation"])

    def test_pre_policy_approve_counts(self):
        out = self._suggest(self._judge("PLTR", 3, rec="APPROVE", blocked=False))
        self.assertEqual(len(out), 1)

    def test_capped_at_five(self):
        judge = {}
        for i in range(8):
            for d, v in self._judge(f"S{i}", 3).items():
                judge.setdefault(d, {"scores": {}})["scores"].update(v["scores"])
        research = _history(3, lambda i: {f"S{n}": _research() for n in range(8)})
        self.assertEqual(len(ur.suggest_additions(self.CFG, TODAY, research, judge, {})), 5)


class TestAddAndCut(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.cfg_path = os.path.join(self.tmp, "core_universe.json")
        self.wl_path = os.path.join(self.tmp, "watchlist.json")
        self.csv_path = os.path.join(self.tmp, "changes.csv")
        with open(self.cfg_path, "w") as f:
            json.dump({"status": "approved",
                       "core": {"AAPL": _entry(), "MSFT": _entry()},
                       "hedge": {"SQQQ": "h"}, "probation": {"NOK": "p"}}, f)
        with open(self.wl_path, "w") as f:
            json.dump({"watchlist": [{"symbol": "AAPL"}, {"symbol": "NOK"}]}, f)

    def _cfg(self):
        with open(self.cfg_path) as f:
            return json.load(f)

    def _add(self, sym, **kw):
        ur.add_symbol(sym, "because", today="2026-12-07", config_path=self.cfg_path,
                      watchlist_path=self.wl_path, changes_csv=self.csv_path, **kw)

    def _cut(self, sym):
        ur.cut_symbol(sym, "weak", today="2026-12-07", config_path=self.cfg_path,
                      changes_csv=self.csv_path)

    def test_add_puts_the_name_in_core_with_a_date(self):
        self._add("pltr")
        self.assertEqual(self._cfg()["core"]["PLTR"], {"added": "2026-12-07", "why": "because"})
        self.assertIsNotNone(universe_policy.load_config(self.cfg_path))

    def test_add_marks_speculative(self):
        self._add("RKLB", speculative=True)
        self.assertTrue(self._cfg()["core"]["RKLB"]["speculative"])

    def test_add_moves_a_probation_name_up(self):
        self._add("NOK")
        cfg = self._cfg()
        self.assertIn("NOK", cfg["core"])
        self.assertNotIn("NOK", cfg["probation"])

    def test_add_puts_a_new_name_on_the_watchlist_with_a_smaller_size_if_speculative(self):
        self._add("RKLB", speculative=True)
        self._add("PLTR")
        with open(self.wl_path) as f:
            wl = {w["symbol"]: w for w in json.load(f)["watchlist"]}
        self.assertEqual(wl["RKLB"]["max_allocation_pct"], 2)
        self.assertEqual(wl["PLTR"]["max_allocation_pct"], 3)

    def test_add_does_not_duplicate_an_existing_watchlist_entry(self):
        self._add("NOK")
        with open(self.wl_path) as f:
            self.assertEqual([w["symbol"] for w in json.load(f)["watchlist"]].count("NOK"), 1)

    def test_add_refuses_core_and_hedge_names(self):
        for sym in ("AAPL", "SQQQ"):
            with self.assertRaises(ValueError):
                self._add(sym)

    def test_cut_moves_the_name_to_probation_and_keeps_it_researched(self):
        self._cut("MSFT")
        cfg = self._cfg()
        self.assertNotIn("MSFT", cfg["core"])
        self.assertIn("weak", cfg["probation"]["MSFT"])

    def test_cut_refuses_a_name_not_in_core(self):
        with self.assertRaises(ValueError):
            self._cut("NOK")

    def test_cutting_the_last_core_name_leaves_the_old_config_in_place(self):
        self._cut("MSFT")
        before = _read(self.cfg_path)
        with self.assertRaises(ValueError):
            self._cut("AAPL")
        self.assertEqual(_read(self.cfg_path), before)

    def test_changes_are_logged(self):
        self._add("PLTR")
        self._cut("MSFT")
        with open(self.csv_path) as f:
            rows = list(csv.DictReader(f))
        self.assertEqual([(r["action"], r["symbol"]) for r in rows], [("add", "PLTR"), ("cut", "MSFT")])


class TestReviewNeverEditsTheConfig(unittest.TestCase):

    def test_build_and_write_leave_core_universe_untouched(self):
        with tempfile.TemporaryDirectory() as d:
            cfg_path = os.path.join(d, "core_universe.json")
            logs = os.path.join(d, "logs")
            os.makedirs(logs)
            with open(cfg_path, "w") as f:
                json.dump({"status": "approved", "core": {"AAA": _entry()}}, f)
            before = _read(cfg_path)
            review = ur.build_review(today=TODAY, logs_dir=logs, config_path=cfg_path)
            ur.write_review(review, proposal_dir=os.path.join(d, "proposals"))
            self.assertEqual(_read(cfg_path), before)
            self.assertEqual(review["core_size"], 1)
            self.assertIn("nothing here has been changed", ur.render_markdown(review))

    def test_notification_is_skipped_when_there_is_nothing_to_say(self):
        review = {"suggested_cuts": [], "suggested_adds": []}
        with mock.patch("notify.send") as send:
            self.assertFalse(ur.notify_review(review))
        send.assert_not_called()

    def test_notification_names_the_symbols(self):
        review = {"suggested_cuts": [{"symbol": "AAA"}], "suggested_adds": [{"symbol": "PLTR"}]}
        with mock.patch("notify.send", return_value=True) as send:
            ur.notify_review(review)
        body = send.call_args[0][1]
        self.assertIn("AAA", body)
        self.assertIn("PLTR", body)


if __name__ == "__main__":
    unittest.main()
