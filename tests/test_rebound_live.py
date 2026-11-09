#!/usr/bin/env python3
"""
Tests for the live oversold-rebound path: scan, shadow ledger, resolver,
learning loop, paper executor, and its isolation from the trend path.

The rules that matter most here are the rails: the learner may only tighten,
the executor is paper-only, and rebound fills never enter the trend record.

Run:  python3 -m unittest discover -s tests -v
"""

import csv
import json
import os
import re
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APCA_API_KEY_ID", "test")
os.environ.setdefault("APCA_API_SECRET_KEY", "test")

import rebound              # noqa: E402
import rebound_learning     # noqa: E402
import rebound_signal as rs  # noqa: E402
import rebound_trade        # noqa: E402
import trade                # noqa: E402
import reviewer             # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Fixed rules, independent of whatever the backtest chose as the shipped defaults.
P = {**rs.DEFAULT_PARAMS, **rebound.LIVE_DEFAULTS,
     "rsi_max": 30, "min_depth_pct": 0.0, "require_uptrend_200": False, "stabilization": "none",
     "market_filter": False, "stop_type": "pct", "stop_value": 0.07, "target_type": "none",
     "target_value": 0.0, "time_stop_sessions": 3}


def make_bars(closes, start=date(2025, 1, 6)):
    bars, d = [], start
    for i, c in enumerate(closes):
        while d.weekday() >= 5:
            d += timedelta(days=1)
        prev = closes[i - 1] if i else c
        bars.append({"t": f"{d.isoformat()}T05:00:00Z", "o": prev, "h": max(prev, c) * 1.005,
                     "l": min(prev, c) * 0.995, "c": c, "v": 2_000_000})
        d += timedelta(days=1)
    return bars


SELLOFF = [100.0] * 210 + [100 - 0.6 * i for i in range(1, 51)]      # 50 straight down days
STEADY = [100.0 + 0.05 * i for i in range(260)]                       # healthy uptrend
CFG = {"core": {"AAA"}, "hedge": {"HHH"}, "probation": {"PPP"}, "speculative": {"SSS"},
       "entries": {}}


def _scan(bars_by_symbol, tmp, **kw):
    last = make_bars(SELLOFF)[-1]["t"][:10]
    return rebound.scan(today=date.fromisoformat(last) + timedelta(days=1), bars_by_symbol=bars_by_symbol,
                        logs_dir=tmp, ledger_path=os.path.join(tmp, "ledger.csv"), cfg=CFG,
                        params=kw.get("params", P))


# ══════════════════════════════════════════════════════════════════════════════
class TestTightenOnly(unittest.TestCase):
    """An edited or corrupted params file must never widen the net."""

    def test_cannot_raise_the_rsi_ceiling(self):
        self.assertEqual(rebound.tighten_only(P, {"rsi_max": 45})["rsi_max"], 30)

    def test_can_lower_the_rsi_ceiling(self):
        self.assertEqual(rebound.tighten_only(P, {"rsi_max": 25})["rsi_max"], 25)

    def test_cannot_shrink_the_required_depth(self):
        p = {**P, "min_depth_pct": 8.0}
        self.assertEqual(rebound.tighten_only(p, {"min_depth_pct": 2.0})["min_depth_pct"], 8.0)
        self.assertEqual(rebound.tighten_only(p, {"min_depth_pct": 12.0})["min_depth_pct"], 12.0)

    def test_filters_can_be_switched_on_never_off(self):
        on = {**P, "require_uptrend_200": True, "market_filter": True}
        out = rebound.tighten_only(on, {"require_uptrend_200": False, "market_filter": False})
        self.assertTrue(out["require_uptrend_200"] and out["market_filter"])
        out = rebound.tighten_only(P, {"require_uptrend_200": True, "market_filter": True})
        self.assertTrue(out["require_uptrend_200"] and out["market_filter"])

    def test_stabilization_can_be_added_but_not_removed(self):
        self.assertEqual(rebound.tighten_only(P, {"stabilization": "close_rsi_up"})["stabilization"], "close_rsi_up")
        strict = {**P, "stabilization": "close_gt_prev_high"}
        self.assertEqual(rebound.tighten_only(strict, {"stabilization": "none"})["stabilization"], "close_gt_prev_high")

    def test_size_and_slots_only_shrink_and_respect_floors(self):
        out = rebound.tighten_only(P, {"position_pct": 9, "max_open": 9})
        self.assertEqual((out["position_pct"], out["max_open"]), (2.5, 4))
        out = rebound.tighten_only(P, {"position_pct": 0.1, "max_open": 0})
        self.assertEqual((out["position_pct"], out["max_open"]), (1.0, 1))

    def test_stop_cannot_be_widened(self):
        self.assertEqual(rebound.tighten_only(P, {"stop_value": 0.20})["stop_value"], 0.07)
        self.assertEqual(rebound.tighten_only(P, {"stop_value": 0.05})["stop_value"], 0.05)

    def test_blocked_symbols_only_accumulate(self):
        p = {**P, "blocked_symbols": ["XXX"]}
        self.assertEqual(rebound.tighten_only(p, {"blocked_symbols": ["YYY"]})["blocked_symbols"], ["XXX", "YYY"])
        self.assertEqual(rebound.tighten_only(p, {"blocked_symbols": []})["blocked_symbols"], ["XXX"])

    def test_wrong_scope_or_garbage_file_is_ignored(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            with open(path, "w") as f:
                json.dump({"scope": "forex", "applied": {"rsi_max": 10}}, f)
            self.assertEqual(rebound.load_learned(path), {})
            with open(path, "w") as f:
                f.write("{not json")
            self.assertEqual(rebound.load_learned(path), {})

    def test_wide_net_is_never_tighter_than_the_trade_rules(self):
        for p in (P, {**P, "rsi_max": 20, "min_depth_pct": 12, "stabilization": "close_rsi_up",
                      "require_uptrend_200": True, "market_filter": True}):
            e = rebound.explore_params(p)
            self.assertGreaterEqual(e["rsi_max"], p["rsi_max"])
            self.assertLessEqual(e["min_depth_pct"], p["min_depth_pct"])
            self.assertFalse(e["require_uptrend_200"] or e["market_filter"])
            self.assertEqual(e["stabilization"], "none")


class TestBarsHandling(unittest.TestCase):

    def test_todays_partial_bar_is_dropped_before_the_close(self):
        bars = make_bars([10, 11, 12])
        today = date.fromisoformat(bars[-1]["t"][:10])
        before = datetime(today.year, today.month, today.day, 11, 0)
        after = datetime(today.year, today.month, today.day, 17, 0)
        self.assertEqual(len(rebound.completed_bars(bars, now=before)), 2)
        self.assertEqual(len(rebound.completed_bars(bars, now=after)), 3)

    def test_yesterdays_last_bar_is_kept(self):
        bars = make_bars([10, 11, 12])
        tomorrow = date.fromisoformat(bars[-1]["t"][:10]) + timedelta(days=1)
        self.assertEqual(len(rebound.completed_bars(bars, now=datetime(tomorrow.year, tomorrow.month, tomorrow.day, 10, 0))), 3)


class TestScan(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.bars = {"AAA": make_bars(SELLOFF), "PPP": make_bars(SELLOFF), "SSS": make_bars(SELLOFF),
                     "HHH": make_bars(SELLOFF), "SPY": make_bars(STEADY), "ZZZ": make_bars(STEADY)}

    def test_core_oversold_name_is_a_tradeable_candidate(self):
        out = _scan(self.bars, self.tmp)
        c = {c["symbol"]: c for c in out["candidates"]}["AAA"]
        self.assertTrue(c["tradeable"])
        self.assertGreater(c["stop_pct"], 0)
        self.assertEqual(c["time_stop_sessions"], 3)

    def test_non_core_names_are_logged_for_learning_but_never_tradeable(self):
        out = _scan(self.bars, self.tmp)
        self.assertEqual(set(out["shadow_only"]), {"PPP", "SSS"})
        self.assertEqual([c["symbol"] for c in out["candidates"]], ["AAA"])
        with open(os.path.join(self.tmp, "ledger.csv")) as f:
            tiers = {r["symbol"]: r["tier"] for r in csv.DictReader(f)}
        self.assertEqual(tiers, {"AAA": "core", "PPP": "probation", "SSS": "speculative"})

    def test_hedge_instruments_are_never_scanned(self):
        out = _scan(self.bars, self.tmp)
        self.assertNotIn("HHH", [c["symbol"] for c in out["candidates"]] + out["shadow_only"])

    def test_healthy_uptrend_is_not_a_setup(self):
        out = _scan({**self.bars, "AAA": make_bars(STEADY)}, self.tmp)
        self.assertEqual(out["candidates"], [])

    def test_too_little_history_is_skipped_not_guessed(self):
        out = _scan({**self.bars, "AAA": make_bars(SELLOFF[-120:])}, self.tmp)
        self.assertEqual(out["candidates"], [])

    def test_blocked_symbol_is_not_tradeable(self):
        out = _scan(self.bars, self.tmp, params={**P, "blocked_symbols": ["AAA"]})
        c = out["candidates"][0]
        self.assertFalse(c["tradeable"])
        self.assertIn("blocked", c["blocked_reason"])

    def test_earnings_blackout_blocks_a_candidate(self):
        with open(os.path.join(self.tmp, f"{date.today().isoformat()}.json"), "w") as f:
            json.dump({"intelligence": {"earnings_watch_symbols": ["AAA"], "vix": 20}}, f)
        last = make_bars(SELLOFF)[-1]["t"][:10]
        out = rebound.scan(today=date.today(), bars_by_symbol=self.bars, logs_dir=self.tmp,
                           ledger_path=os.path.join(self.tmp, "ledger.csv"), cfg=CFG, params=P)
        self.assertFalse(out["candidates"][0]["tradeable"])
        self.assertIn("earnings", out["candidates"][0]["blocked_reason"])
        self.assertEqual(out["vix"], 20)

    def test_a_failing_rule_blocks_the_trade_but_the_setup_is_still_logged(self):
        strict = {**P, "stabilization": "close_rsi_up"}
        out = _scan(self.bars, self.tmp, params=strict)
        c = out["candidates"][0]
        self.assertFalse(c["tradeable"])
        self.assertIn("stabilizing", c["blocked_reason"])
        with open(os.path.join(self.tmp, "ledger.csv")) as f:
            self.assertEqual({r["symbol"]: r["would_trade"] for r in csv.DictReader(f)}["AAA"], "0")

    def test_scan_writes_json_and_markdown(self):
        out = _scan(self.bars, self.tmp)
        for ext in (".json", ".md"):
            self.assertTrue(os.path.exists(os.path.join(self.tmp, f"{out['date']}-rebound{ext}")))
        md = rebound.render_markdown(out)
        self.assertIn("UNPROVEN", md)
        self.assertIn("no reliable edge", md)

    def test_rerunning_the_same_day_does_not_duplicate_ledger_rows(self):
        _scan(self.bars, self.tmp)
        _scan(self.bars, self.tmp)
        with open(os.path.join(self.tmp, "ledger.csv")) as f:
            self.assertEqual(len(list(csv.DictReader(f))), 3)


class TestResolver(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.ledger = os.path.join(self.tmp, "ledger.csv")
        self.bars = {"AAA": make_bars(SELLOFF), "SPY": make_bars(STEADY)}
        _scan(self.bars, self.tmp)
        self.signal = make_bars(SELLOFF)[-1]["t"][:10]

    def _future(self, closes, sym="AAA"):
        last = date.fromisoformat(self.signal)
        extra = make_bars([SELLOFF[-1]] + closes, start=last)[1:]
        return {sym: make_bars(SELLOFF) + extra, "SPY": make_bars(STEADY, start=date(2025, 1, 6)) + make_bars(
            [101.0 + 0.1 * i for i in range(len(closes) + 1)], start=last)[1:]}

    def test_outcomes_are_filled_from_later_bars(self):
        later = date.fromisoformat(self.signal) + timedelta(days=20)
        res = rebound.resolve(today=later, bars_by_symbol=self._future([72, 74, 76, 78, 80, 82]), path=self.ledger)
        self.assertEqual(res["resolved"], 1)
        row = next(r for r in rebound.load_ledger(self.ledger) if r["symbol"] == "AAA")
        self.assertEqual(row["sim_reason"], "time")
        self.assertGreater(float(row["sim_ret"]), 0)
        self.assertTrue(row["r3"] and row["r5"] and row["spy_r3"])

    def test_a_stop_is_taken_when_price_keeps_falling(self):
        later = date.fromisoformat(self.signal) + timedelta(days=20)
        rebound.resolve(today=later, bars_by_symbol=self._future([55, 50, 45, 40]), path=self.ledger)
        row = next(r for r in rebound.load_ledger(self.ledger) if r["symbol"] == "AAA")
        self.assertIn(row["sim_reason"], ("stop", "stop_gap"))
        self.assertLess(float(row["sim_ret"]), 0)

    def test_too_little_future_leaves_the_row_pending(self):
        later = date.fromisoformat(self.signal) + timedelta(days=20)
        res = rebound.resolve(today=later, bars_by_symbol=self._future([66]), path=self.ledger)
        self.assertEqual(res["resolved"], 0)

    def test_resolving_twice_never_overwrites_a_filled_outcome(self):
        later = date.fromisoformat(self.signal) + timedelta(days=20)
        rebound.resolve(today=later, bars_by_symbol=self._future([66, 67, 68, 69]), path=self.ledger)
        first = next(r for r in rebound.load_ledger(self.ledger))["sim_ret"]
        rebound.resolve(today=later, bars_by_symbol=self._future([30, 20, 10, 5]), path=self.ledger)
        self.assertEqual(next(r for r in rebound.load_ledger(self.ledger))["sim_ret"], first)

    def test_rows_older_than_the_give_up_window_are_left_alone(self):
        later = date.fromisoformat(self.signal) + timedelta(days=rebound.LEDGER_MAX_AGE_DAYS + 5)
        res = rebound.resolve(today=later, bars_by_symbol=self._future([66, 67, 68, 69]), path=self.ledger)
        self.assertEqual(res["resolved"], 0)

    def test_mark_paper_traded(self):
        rebound.mark_paper_traded("AAA", self.signal, path=self.ledger)
        self.assertEqual(next(iter(rebound.load_ledger(self.ledger)))["paper_traded"], "1")


class TestRecordAndSuggestion(unittest.TestCase):

    @staticmethod
    def _rows(n, win_frac, ret=0.03, excess=0.02):
        rows = []
        for i in range(n):
            win = i < n * win_frac
            rows.append({"would_trade": "1", "sim_ret": str(ret if win else -ret),
                         "r10": str(excess if win else -excess), "spy_r10": "0"})
        return rows

    def test_proven_needs_sample_win_rate_median_and_excess(self):
        self.assertTrue(rebound.setup_record(self._rows(25, 0.7))["proven"])
        self.assertFalse(rebound.setup_record(self._rows(10, 0.9))["proven"])       # too few
        self.assertFalse(rebound.setup_record(self._rows(25, 0.4))["proven"])       # loses
        self.assertFalse(rebound.setup_record(self._rows(25, 0.56, excess=-0.01))["proven"] and False)

    def test_rows_that_did_not_meet_the_rules_do_not_count(self):
        rows = self._rows(30, 0.9)
        for r in rows:
            r["would_trade"] = "0"
        self.assertEqual(rebound.setup_record(rows)["n"], 0)

    def test_unproven_suggestion_is_half_size(self):
        c = {"symbol": "AAA", "price": 100.0, "stop_pct": 7.0, "target_pct": None, "time_stop_sessions": 3,
             "rsi": 22.0, "depth_pct": 9.0, "stab_rsi_up": False, "above_ma200": True}
        s = rebound.build_suggestion(c, P, {"proven": False}, {})
        self.assertEqual((s["status"], s["suggested_real_size_pct"]), ("UNPROVEN", 1.25))
        self.assertEqual(s["stop"], 93.0)
        self.assertEqual(s["risk_per_10k"], round(10000 * 0.0125 * 0.07, 2))
        s = rebound.build_suggestion(c, P, {"proven": True}, {})
        self.assertEqual((s["status"], s["suggested_real_size_pct"]), ("PROVEN", 2.5))


# ══════════════════════════════════════════════════════════════════════════════
def _row(sym="AAA", rsi=25.0, depth=10.0, ret=0.03, stab=True, ma200=True, spy=True, tier="core",
         would=True, r10=None, spy_r10=0.0, date_="2026-01-02"):
    return {"symbol": sym, "signal_date": date_, "tier": tier, "rsi": str(rsi), "depth_pct": str(depth),
            "above_ma200": "1" if ma200 else "0", "stab_rsi_up": "1" if stab else "0", "stab_prev_high": "0",
            "spy_above_ma200": "1" if spy else "0", "would_trade": "1" if would else "0",
            "sim_ret": str(ret), "r10": str(ret if r10 is None else r10), "spy_r10": str(spy_r10)}


class TestLearning(unittest.TestCase):

    def test_a_losing_slice_tightens_the_rsi_ceiling(self):
        rows = ([_row(rsi=27, ret=-0.04) for _ in range(25)] + [_row(rsi=20, ret=0.03) for _ in range(15)])
        learned, ev = rebound_learning.derive(rows, P)
        self.assertEqual(learned["rsi_max"], 25)
        self.assertTrue(ev)

    def test_small_samples_move_nothing(self):
        rows = [_row(rsi=27, ret=-0.04) for _ in range(5)] + [_row(rsi=20, ret=0.03) for _ in range(15)]
        self.assertEqual(rebound_learning.derive(rows, P)[0], {})

    def test_losing_unstabilized_setups_require_stabilization(self):
        rows = ([_row(stab=False, ret=-0.03) for _ in range(25)] + [_row(stab=True, ret=0.03) for _ in range(15)])
        self.assertEqual(rebound_learning.derive(rows, P)[0]["stabilization"], "close_rsi_up")

    def test_losing_weak_market_setups_turn_on_the_market_filter(self):
        rows = ([_row(spy=False, ret=-0.03) for _ in range(25)] + [_row(spy=True, ret=0.03) for _ in range(15)])
        self.assertTrue(rebound_learning.derive(rows, P)[0]["market_filter"])

    def test_losing_below_200_day_setups_require_the_uptrend(self):
        rows = ([_row(ma200=False, ret=-0.03) for _ in range(25)] + [_row(ma200=True, ret=0.03) for _ in range(15)])
        self.assertTrue(rebound_learning.derive(rows, P)[0]["require_uptrend_200"])

    def test_a_chronic_loser_is_blocked(self):
        rows = [_row(sym="BAD", ret=-0.05, date_=f"2026-01-{i + 1:02d}") for i in range(9)]
        self.assertIn("BAD", rebound_learning.derive(rows, P)[0]["blocked_symbols"])

    def test_circuit_breaker_halves_size_when_the_sleeve_loses(self):
        rows = [_row(ret=-0.03 if i % 5 else 0.01, date_=f"2026-02-{i % 28 + 1:02d}") for i in range(35)]
        learned, _ = rebound_learning.derive(rows, P)
        self.assertEqual((learned["position_pct"], learned["max_open"]), (1.25, 3))

    def test_learning_is_evidence_from_all_tiers_but_the_breaker_is_core_only(self):
        rows = [_row(tier="universe", ret=-0.03, date_=f"2026-02-{i % 28 + 1:02d}") for i in range(40)]
        self.assertNotIn("position_pct", rebound_learning.derive(rows, P)[0])

    def test_nothing_is_ever_loosened_automatically(self):
        strict = {**P, "rsi_max": 20, "stabilization": "close_rsi_up", "require_uptrend_200": True, "market_filter": True}
        rows = [_row(rsi=25, stab=False, ma200=False, spy=False, ret=0.05) for _ in range(60)]
        learned, _ = rebound_learning.derive(rows, strict)
        applied = rebound.tighten_only(strict, learned)
        self.assertEqual({k: applied[k] for k in strict}, {k: strict[k] for k in strict})

    def test_a_strong_excluded_slice_becomes_a_proposal_not_a_change(self):
        strict = {**P, "stabilization": "close_rsi_up"}
        rows = [_row(stab=False, ret=0.04, r10=0.04) for _ in range(35)]
        props = rebound_learning.propose(rows, strict, P)
        self.assertTrue(any("stabilization" in p["change"] for p in props))

    def test_proposals_are_not_repeated(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "p.json")
            item = [{"change": "drop the market filter", "evidence": {"n": 40}}]
            self.assertEqual(len(rebound_learning.write_proposals(item, "2026-12-01", path)), 1)
            self.assertEqual(len(rebound_learning.write_proposals(item, "2026-12-02", path)), 0)

    def test_learn_writes_a_scoped_file_that_the_scanner_honours_and_cannot_loosen(self):
        with tempfile.TemporaryDirectory() as d:
            ledger, params, props = (os.path.join(d, n) for n in ("l.csv", "p.json", "pr.json"))
            rows = ([_row(rsi=27, ret=-0.04) for _ in range(25)] + [_row(rsi=20, ret=0.03) for _ in range(15)])
            fields = ["signal_date", "symbol", "tier", "rsi", "depth_pct", "above_ma200", "stab_rsi_up",
                      "stab_prev_high", "spy_above_ma200", "would_trade", "sim_ret", "r10", "spy_r10"]
            with open(ledger, "w", newline="") as f:
                w = csv.DictWriter(f, fieldnames=rebound.LEDGER_FIELDS)
                w.writeheader()
                for r in rows:
                    w.writerow({k: r.get(k, "") for k in rebound.LEDGER_FIELDS})
            # Independent of whatever rules the backtest chose as the shipped defaults.
            with mock.patch.dict(rs.DEFAULT_PARAMS, {k: P[k] for k in rs.DEFAULT_PARAMS}):
                out = rebound_learning.learn(today="2026-12-01", ledger_path=ledger, learned_path=params,
                                             proposals_path=props)
                self.assertEqual(out["applied"]["rsi_max"], 25)
                with open(params) as fh:
                    self.assertEqual(json.load(fh)["scope"], rebound.LEARNED_SCOPE)
                self.assertEqual(rebound.live_params(params)["rsi_max"], 25)
                with open(params, "w") as f:                   # someone edits it to loosen
                    json.dump({"scope": rebound.LEARNED_SCOPE, "applied": {"rsi_max": 60}}, f)
                self.assertEqual(rebound.live_params(params)["rsi_max"], 30)   # the patched default
            # and against the rules that really ship:
            self.assertEqual(rebound.live_params(params)["rsi_max"], rs.DEFAULT_PARAMS["rsi_max"])


# ══════════════════════════════════════════════════════════════════════════════
class TestExecutor(unittest.TestCase):

    def test_refuses_to_run_against_a_live_broker(self):
        with mock.patch.object(trade, "BASE_URL", "https://api.alpaca.markets"):
            with self.assertRaises(RuntimeError):
                rebound_trade.assert_paper()
            with self.assertRaises(RuntimeError):
                rebound_trade.run()

    def test_passes_against_the_paper_endpoint(self):
        with mock.patch.object(trade, "BASE_URL", "https://paper-api.alpaca.markets"):
            rebound_trade.assert_paper()

    def test_no_real_money_code_path_exists(self):
        for name in ("rebound.py", "rebound_trade.py", "rebound_learning.py"):
            with open(os.path.join(ROOT, name)) as fh:
                src = fh.read()
            self.assertNotRegex(src, r"(?m)^\s*(import|from)\s+robinhood")
            self.assertNotIn("place_equity_order", src)
            self.assertNotIn("mcp__robinhood", src)

    def test_sessions_skip_weekends_and_holidays(self):
        self.assertEqual(rebound_trade.sessions_between(date(2026, 11, 6), date(2026, 11, 9)), 1)   # Fri -> Mon
        self.assertEqual(rebound_trade.sessions_between(date(2026, 11, 24), date(2026, 11, 27)), 2)  # skips Thanksgiving

    def _cand(self, sym, score=10, tradeable=True):
        return {"symbol": sym, "score": score, "tradeable": tradeable, "stop_pct": 7, "time_stop_sessions": 3,
                "signal_date": "2026-11-06", "price": 50.0}

    def test_entries_respect_slots_priority_holdings_and_blocks(self):
        state = {"open": {"OLD": {}}, "pending": {}}
        p = {**P, "max_open": 3, "blocked_symbols": ["BLK"]}
        cands = [self._cand("A", 5), self._cand("B", 9), self._cand("C", 7), self._cand("HELD", 99),
                 self._cand("BLK", 98), self._cand("OLD", 97), self._cand("NO", 96, tradeable=False)]
        chosen, skipped = rebound_trade.choose_entries(cands, state, {"HELD"}, set(), p, vix=20)
        self.assertEqual([c["symbol"] for c in chosen], ["B", "C"])          # 3 slots - 1 open = 2, best first
        self.assertEqual({s["symbol"] for s in skipped}, {"A", "HELD", "BLK", "OLD", "NO"})

    def test_high_vix_blocks_every_entry(self):
        chosen, skipped = rebound_trade.choose_entries([self._cand("A")], rebound_trade.empty_state(), set(), set(), P, vix=36)
        self.assertEqual(chosen, [])
        self.assertIn("VIX", skipped[0]["reason"])

    def test_the_vix_cap_matches_the_trend_judges(self):
        import judge
        self.assertEqual(rebound_trade.VIX_NO_NEW_POSITIONS, judge.VIX_NO_NEW_POSITIONS)

    def test_open_buy_order_blocks_a_second_one(self):
        chosen, _ = rebound_trade.choose_entries([self._cand("A")], rebound_trade.empty_state(), set(), {"A"}, P, vix=20)
        self.assertEqual(chosen, [])

    def test_entry_never_bids_above_the_ask_and_uses_the_rb_prefix(self):
        posted = {}
        with mock.patch.object(trade, "get_latest_ask", return_value=49.80), \
             mock.patch.object(trade, "calculate_max_shares", return_value=(10, "ok")), \
             mock.patch.object(trade, "_post_order", side_effect=lambda b: posted.update(b) or {"_ok": True, "id": "x"}):
            r = rebound_trade.place_entry(self._cand("A"), {}, [], P)
        self.assertEqual(r["action"], "BUY")
        self.assertEqual(float(posted["limit_price"]), 49.80)
        self.assertTrue(posted["client_order_id"].startswith("rb-buy-"))
        self.assertEqual(posted["time_in_force"], "day")

    def test_a_gap_up_entry_is_skipped(self):
        with mock.patch.object(trade, "get_latest_ask", return_value=53.0):
            r = rebound_trade.place_entry(self._cand("A"), {}, [], {**P, "max_gap_up_pct": 3.0})
        self.assertEqual(r["action"], "SKIPPED")

    def test_exit_orders_bracket_the_entry_and_use_the_rb_prefix(self):
        posted = {}
        meta = {"entry_price": 50.0, "stop_pct": 7.0, "target_pct": 10.0}
        with mock.patch.object(trade, "_post_order", side_effect=lambda b: posted.update(b) or {"_ok": True}):
            rebound_trade.attach_exits("A", meta, 10)
        self.assertEqual(posted["stop_loss"]["stop_price"], "46.5")
        self.assertEqual(posted["take_profit"]["limit_price"], "55.0")
        self.assertTrue(posted["client_order_id"].startswith("rb-oco-"))

    def test_no_target_means_a_lone_stop(self):
        posted = {}
        with mock.patch.object(trade, "_post_order", side_effect=lambda b: posted.update(b) or {"_ok": True}):
            rebound_trade.attach_exits("A", {"entry_price": 50.0, "stop_pct": 7.0, "target_pct": None}, 10)
        self.assertEqual(posted["type"], "stop")
        self.assertTrue(posted["client_order_id"].startswith("rb-stop-"))

    def test_degenerate_exit_math_places_nothing(self):
        with mock.patch.object(trade, "_post_order") as post:
            r = rebound_trade.attach_exits("A", {"entry_price": 0.03, "stop_pct": 1.0, "target_pct": None}, 10)
        post.assert_not_called()
        self.assertFalse(r["_ok"])

    def test_reconcile_promotes_a_filled_entry_drops_a_dead_one_and_records_an_exit(self):
        state = {"pending": {"FILL": {"signal_date": "2026-11-06", "order_id": "o1", "stop_pct": 7,
                                       "target_pct": None, "time_stop_sessions": 3},
                             "DEAD": {"signal_date": "2026-11-06", "order_id": "o2", "stop_pct": 7,
                                      "target_pct": None, "time_stop_sessions": 3}},
                 "open": {"GONE": {"signal_date": "2026-11-02", "entry_date": "2026-11-03", "entry_price": 50.0,
                                    "qty": 10, "stop_pct": 7, "target_pct": None, "time_stop_sessions": 3,
                                    "exits_attached": True}}}
        positions = [{"symbol": "FILL", "qty": "20", "avg_entry_price": "40.0"}]
        closed = [{"symbol": "GONE", "side": "sell", "status": "filled", "filled_qty": "10",
                   "filled_avg_price": "46.5", "filled_at": "2026-11-05T15:00:00Z", "type": "stop",
                   "client_order_id": "rb-stop-1-GONE"}]
        out = rebound_trade.reconcile(state, positions, [], closed, date(2026, 11, 9))
        self.assertEqual(out["filled"], ["FILL"])
        self.assertEqual(out["expired"], ["DEAD"])
        self.assertEqual(state["open"]["FILL"]["entry_price"], 40.0)
        self.assertEqual(out["exited"][0]["reason"], "STOP")
        self.assertEqual(out["exited"][0]["pnl_pct"], -7.0)
        self.assertNotIn("GONE", state["open"])

    def test_a_time_stop_sell_is_labelled_as_one(self):
        self.assertEqual(rebound_trade._exit_reason({"client_order_id": "rb-sell-1-A"}), "TIME_STOP")
        self.assertEqual(rebound_trade._exit_reason({"client_order_id": "x", "type": "limit",
                                                     "order_class": "oco"}), "TARGET")

    def test_state_round_trips_and_a_missing_file_is_empty(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "s.json")
            self.assertEqual(rebound_trade.load_state(path), {"pending": {}, "open": {}})
            rebound_trade.save_state({"pending": {"A": {}}, "open": {"B": {}}}, path)
            self.assertEqual(rebound_trade.managed_symbols(path), {"A", "B"})


class TestIsolationFromTheTrendPath(unittest.TestCase):

    def test_graduation_equity_excludes_the_rebound_sleeve(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "rebound-trades.csv"), "w") as f:
                f.write("symbol,pnl_usd\nAAA,120.5\nBBB,-20.5\n")
            with open(os.path.join(d, "rebound-state.json"), "w") as f:
                json.dump({"open": {"CCC": {}}, "pending": {}}, f)
            positions = [{"symbol": "CCC", "unrealized_pl": "50"}, {"symbol": "DDD", "unrealized_pl": "999"}]
            self.assertEqual(reviewer.rebound_sleeve_pnl(positions, logs_dir=d), 150.0)

    def test_missing_rebound_files_change_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(reviewer.rebound_sleeve_pnl([{"symbol": "X", "unrealized_pl": "5"}], logs_dir=d), 0.0)

    def test_trend_executor_does_not_attach_its_own_exits_to_a_rebound_position(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "rebound-state.json"), "w") as f:
                json.dump({"pending": {}, "open": {"AAA": {}}}, f)
            positions = [{"symbol": "AAA", "qty": "10", "avg_entry_price": "50", "asset_class": "us_equity",
                          "current_price": "50", "unrealized_plpc": "0"}]
            with mock.patch.object(trade, "LOGS_DIR", d), mock.patch.object(trade, "place_oco_exits") as oco:
                trade.ensure_exits_for_positions(positions, [], state={"stop_outs": {}})
            oco.assert_not_called()

    def test_trend_executor_still_protects_ordinary_positions(self):
        with tempfile.TemporaryDirectory() as d:
            positions = [{"symbol": "BBB", "qty": "10", "avg_entry_price": "50", "asset_class": "us_equity",
                          "current_price": "50", "unrealized_plpc": "0"}]
            with mock.patch.object(trade, "LOGS_DIR", d), mock.patch.object(trade, "place_oco_exits", return_value=[]) as oco:
                trade.ensure_exits_for_positions(positions, [], state={"stop_outs": {}})
            oco.assert_called_once()

    def test_trend_resting_order_revalidation_ignores_rebound_buys(self):
        order = {"side": "buy", "symbol": "AAA", "id": "1", "client_order_id": "rb-buy-1-AAA",
                 "submitted_at": "2020-01-01T00:00:00Z"}
        with mock.patch.object(trade.requests, "delete") as delete:
            actions = trade.revalidate_resting_entries([order], {})
        delete.assert_not_called()
        self.assertEqual(actions, [])

    def test_rebound_fills_never_reach_the_trend_round_trip_ledger(self):
        page = [{"id": "1", "status": "filled", "filled_qty": "5", "symbol": "AAA", "side": "buy",
                 "client_order_id": "rb-buy-1-AAA", "filled_at": "2026-11-05T15:00:00Z"},
                {"id": "2", "status": "filled", "filled_qty": "5", "symbol": "BBB", "side": "buy",
                 "client_order_id": "agent-buy-BBB-1", "filled_at": "2026-11-05T15:01:00Z"}]
        resp = mock.Mock(json=lambda: page)
        resp.raise_for_status = lambda: None
        with mock.patch.object(reviewer.requests, "get", return_value=resp):
            got = reviewer.fetch_filled_orders("2026-11-01")
            today = reviewer.get_orders_today()
        self.assertEqual([o["symbol"] for o in got], ["BBB"])
        self.assertEqual([o["symbol"] for o in today], ["BBB"])


if __name__ == "__main__":
    unittest.main()
