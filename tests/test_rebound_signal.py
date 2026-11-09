#!/usr/bin/env python3
"""
Tests for rebound_signal.py, the oversold-rebound rules shared by the live path
and the backtest. No network.

Run:  python3 -m unittest discover -s tests -v
"""

import os
import random
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("APCA_API_KEY_ID", "test")
os.environ.setdefault("APCA_API_SECRET_KEY", "test")

import rebound_signal as rs  # noqa: E402


# Tests must not depend on whichever config the backtest last selected.
NEUTRAL_ENTRY = dict(rsi_max=30, min_depth_pct=0.0, require_uptrend_200=False, stabilization="none",
                     market_filter=False)
NEUTRAL_EXIT = dict(stop_type="pct", stop_value=0.5, target_type="none", target_value=0.0,
                    time_stop_sessions=5)


def _setup(f, p=None, market=None):
    return rs.setup(f, {**NEUTRAL_ENTRY, **(p or {})}, market)


def _levels(entry, f, p=None):
    return rs.exit_levels(entry, f, {**NEUTRAL_EXIT, **(p or {})})


def _walk(n=320, seed=7, start=100.0):
    rng = random.Random(seed)
    bars, px = [], start
    for _ in range(n):
        o = px * (1 + rng.uniform(-0.01, 0.01))
        c = o * (1 + rng.uniform(-0.03, 0.03))
        h, l = max(o, c) * (1 + rng.uniform(0, 0.01)), min(o, c) * (1 - rng.uniform(0, 0.01))
        bars.append({"o": o, "h": h, "l": l, "c": c, "v": rng.randint(1_000_000, 5_000_000)})
        px = c
    return bars


def _mean(xs):
    return sum(xs) / len(xs)


class TestIndicatorParity(unittest.TestCase):

    def test_matches_a_slice_based_reference_implementation(self):
        """Reference written the way research.compute_technicals is: slice, then average."""
        bars = _walk()
        f = rs.features(bars)
        c = [b["c"] for b in bars]
        self.assertAlmostEqual(f["ma20"], _mean(c[-20:]), places=9)
        self.assertAlmostEqual(f["ma50"], _mean(c[-50:]), places=9)
        self.assertAlmostEqual(f["ma200"], _mean(c[-200:]), places=9)
        self.assertAlmostEqual(f["ma20_prev"], _mean(c[-21:-1]), places=9)

        gains = [max(c[i] - c[i - 1], 0) for i in range(len(c) - 30 + 1, len(c))]
        losses = [max(c[i - 1] - c[i], 0) for i in range(len(c) - 30 + 1, len(c))]
        rsi = round(100 - 100 / (1 + _mean(gains[-14:]) / _mean(losses[-14:])), 2)
        self.assertAlmostEqual(f["rsi"], rsi, places=2)

        trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - c[i - 1]), abs(bars[i]["l"] - c[i - 1]))
               for i in range(1, len(bars))]
        self.assertAlmostEqual(f["atr"], _mean(trs[-14:]), places=9)

    def test_matches_research_compute_technicals(self):
        import research
        for seed in (1, 2, 3):
            bars = _walk(seed=seed)
            f = rs.features(bars)
            ref = research.compute_technicals({"bars": [{"c": b["c"], "h": b["h"], "l": b["l"], "v": b["v"]}
                                                        for b in bars]})
            self.assertAlmostEqual(f["ma20"], ref["ma20"], places=3)
            self.assertAlmostEqual(f["ma50"], ref["ma50"], places=3)
            self.assertAlmostEqual(f["rsi"], ref["rsi"], delta=0.011)
            self.assertAlmostEqual(f["atr"], ref["atr"], places=3)
            self.assertEqual(f["signal"], ref["signal"])

    def test_rsi_hand_computed(self):
        """14 diffs: seven +2 and seven -1 -> avg gain 1.0, avg loss 0.5, RS 2, RSI 66.67."""
        closes = [100.0] * 191
        for i in range(14):
            closes.append(closes[-1] + (2.0 if i % 2 == 0 else -1.0))
        bars = [{"o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": 1_000_000} for c in closes]
        self.assertAlmostEqual(rs.features(bars)["rsi"], 66.67, places=2)

    def test_all_gains_gives_rsi_100(self):
        bars = [{"o": 100 + i, "h": 101 + i, "l": 99 + i, "c": 100 + i, "v": 1} for i in range(220)]
        self.assertEqual(rs.features(bars)["rsi"], 100.0)

    def test_signal_rule_matches_the_repo(self):
        self.assertEqual(rs.signal_for(110, 105, 100, 104), "STRONG_BUY")
        self.assertEqual(rs.signal_for(102, 105, 100, 104), "WEAK_BUY")
        self.assertEqual(rs.signal_for(95, 105, 100, 104), "STRONG_SELL")
        self.assertEqual(rs.signal_for(102, 99, 100, 100), "WEAK_SELL")
        self.assertEqual(rs.signal_for(102, 100, 100, 99), "HOLD")

    def test_chase_veto_matches_the_judge(self):
        import judge
        for rsi, last, ma20 in ((65, 100, 100), (50, 104, 100), (50, 102, 100), (60, 100, 100), (61, 100, 100)):
            veto, _ = judge.check_chase_veto({"rsi": rsi, "ma20": ma20, "last_close": last}, "X")
            self.assertEqual(rs.passes_chase_veto(rsi, last, ma20), not veto, (rsi, last, ma20))

    def test_too_few_bars_returns_none(self):
        self.assertIsNone(rs.features(_walk(n=150)))
        self.assertIsNone(rs.features([]))


class TestNoLookahead(unittest.TestCase):

    def test_features_at_t_ignore_every_later_bar(self):
        bars = _walk(n=400)
        full = rs.feature_columns(*([b[k] for b in bars] for k in ("o", "h", "l", "c", "v")))
        for t in (210, 260, 330):
            part = rs.feature_columns(*([b[k] for b in bars[:t + 1]] for k in ("o", "h", "l", "c", "v")))
            for key in ("ma20", "ma50", "ma200", "rsi", "atr", "dvol20"):
                self.assertAlmostEqual(full[key][t], part[key][t], places=7, msg=f"{key}@{t}")

    def test_features_do_not_change_when_later_bars_are_appended(self):
        bars = _walk(n=400)
        a = rs.features(bars[:300])
        b = rs.features(bars[:300] + _walk(n=50, seed=99, start=500.0))  # noise that is dropped by slicing
        c = rs.features(bars[:300])
        self.assertEqual(a, c)
        self.assertNotEqual(a["last_close"], b["last_close"])

    def test_entry_is_the_next_open_not_the_signal_close(self):
        f = {"atr": 1.0, "ma20": 100.0}
        base = [{"o": 50.0, "h": 52.0, "l": 49.5, "c": 51.0}] + [{"o": 51, "h": 52, "l": 50.5, "c": 51.5}] * 9
        res = rs.simulate_trade(base, f, NEUTRAL_EXIT, slippage=0.001, cost=0.0)
        self.assertAlmostEqual(res["entry_fill"], 50.0 * 1.001)
        # nothing about the entry day's close/high/low may move the fill
        moved = [dict(base[0], c=1.0, h=999.0)] + base[1:]
        self.assertAlmostEqual(rs.simulate_trade(moved, f, NEUTRAL_EXIT, 0.001, 0.0)["entry_fill"], 50.0 * 1.001)

    def test_short_window_returns_none_rather_than_guessing(self):
        self.assertIsNone(rs.simulate_trade([{"o": 1, "h": 1, "l": 1, "c": 1}] * 3, {"atr": 1, "ma20": 1},
                                            {"time_stop_sessions": 10}))


class TestExitOrdering(unittest.TestCase):
    F = {"atr": 1.0, "ma20": 105.0}
    P = {"stop_type": "pct", "stop_value": 0.05, "target_type": "pct", "target_value": 0.08,
         "time_stop_sessions": 5}

    def _run(self, bars, **over):
        pad = [{"o": 100, "h": 100.5, "l": 99.5, "c": 100}] * 10
        return rs.simulate_trade(bars + pad, self.F, {**self.P, **over}, slippage=0.0, cost=0.0)

    def test_stop_wins_when_stop_and_target_are_both_touched(self):
        r = self._run([{"o": 100, "h": 100.5, "l": 99.5, "c": 100},
                       {"o": 100, "h": 110.0, "l": 90.0, "c": 100}])
        self.assertEqual(r["reason"], "stop")
        self.assertAlmostEqual(r["exit_price"], 95.0)
        self.assertEqual(r["exit_offset"], 1)

    def test_gap_through_the_stop_fills_at_the_open(self):
        r = self._run([{"o": 100, "h": 100.5, "l": 99.5, "c": 100},
                       {"o": 90.0, "h": 92.0, "l": 88.0, "c": 91.0}])
        self.assertEqual(r["reason"], "stop_gap")
        self.assertAlmostEqual(r["exit_price"], 90.0)
        self.assertAlmostEqual(r["ret"], -0.10)

    def test_gap_above_the_target_fills_at_the_open(self):
        r = self._run([{"o": 100, "h": 100.5, "l": 99.5, "c": 100},
                       {"o": 112.0, "h": 113.0, "l": 111.0, "c": 112.5}])
        self.assertEqual(r["reason"], "target_gap")
        self.assertAlmostEqual(r["exit_price"], 112.0)

    def test_target_hit_fills_at_the_target(self):
        r = self._run([{"o": 100, "h": 100.5, "l": 99.5, "c": 100},
                       {"o": 101, "h": 109.0, "l": 100.5, "c": 108.0}])
        self.assertEqual(r["reason"], "target")
        self.assertAlmostEqual(r["exit_price"], 108.0)
        self.assertAlmostEqual(r["ret"], 0.08)

    def test_stop_can_trigger_on_the_entry_day(self):
        r = self._run([{"o": 100, "h": 100.5, "l": 94.0, "c": 96.0}])
        self.assertEqual((r["reason"], r["exit_offset"]), ("stop", 0))
        self.assertAlmostEqual(r["mae"], 94.0 / 100.0 - 1)

    def test_time_stop_exits_at_the_close_of_the_last_session(self):
        flat = [{"o": 100, "h": 101, "l": 99, "c": 100 + i} for i in range(5)]
        r = self._run(flat)
        self.assertEqual((r["reason"], r["exit_offset"]), ("time", 4))
        self.assertAlmostEqual(r["exit_price"], 104.0)

    def test_costs_are_charged_on_both_sides(self):
        flat = [{"o": 100, "h": 101, "l": 99, "c": 100}] * 5
        pad = [{"o": 100, "h": 101, "l": 99, "c": 100}] * 10
        r = rs.simulate_trade(flat + pad, self.F, self.P, slippage=0.001, cost=0.001)
        self.assertAlmostEqual(r["ret"], 100 * 0.999 / (100.1 * 1.001) - 1, places=9)
        self.assertLess(r["ret"], -0.002)


class TestSetupFlags(unittest.TestCase):

    def _f(self, **over):
        f = {"last_close": 90.0, "prev_close": 89.0, "prev_high": 91.0, "ma20": 98.0, "ma20_prev": 98.5,
             "ma50": 100.0, "ma200": 85.0, "rsi": 27.0, "rsi_prev": 25.0, "atr": 2.0, "atr_pct": 2.2,
             "dvol20": 5e7, "signal": "STRONG_SELL", "depth_below_ma50_pct": 10.0}
        f.update(over)
        return f

    def test_basic_oversold_below_ma50_passes(self):
        ok, flags = _setup(self._f(), {"rsi_max": 30, "min_depth_pct": 0})
        self.assertTrue(ok)
        self.assertTrue(flags["below_ma50"] and flags["oversold"])

    def test_above_ma50_never_qualifies(self):
        ok, flags = _setup(self._f(last_close=101.0, depth_below_ma50_pct=-1.0), {"rsi_max": 99})
        self.assertFalse(ok)
        self.assertFalse(flags["below_ma50"])

    def test_rsi_threshold_is_strict(self):
        self.assertFalse(_setup(self._f(rsi=30.0), {"rsi_max": 30})[0])
        self.assertTrue(_setup(self._f(rsi=29.99), {"rsi_max": 30})[0])

    def test_depth_filter(self):
        p = {"rsi_max": 30, "min_depth_pct": 12.0}
        self.assertFalse(_setup(self._f(depth_below_ma50_pct=10.0), p)[0])
        self.assertTrue(_setup(self._f(depth_below_ma50_pct=12.0), p)[0])

    def test_long_term_uptrend_filter(self):
        p = {"rsi_max": 30, "require_uptrend_200": True}
        self.assertTrue(_setup(self._f(ma200=85.0), p)[0])
        ok, flags = _setup(self._f(ma200=95.0), p)
        self.assertFalse(ok)
        self.assertFalse(flags["uptrend_200"])

    def test_stabilization_close_up_and_rsi_turning(self):
        p = {"rsi_max": 30, "stabilization": "close_rsi_up"}
        self.assertTrue(_setup(self._f(last_close=90, prev_close=89, rsi=27, rsi_prev=25), p)[0])
        self.assertFalse(_setup(self._f(last_close=88, prev_close=89), p)[0])
        self.assertFalse(_setup(self._f(rsi=24, rsi_prev=25), p)[0])

    def test_stabilization_close_above_prior_high(self):
        p = {"rsi_max": 30, "stabilization": "close_gt_prev_high"}
        self.assertFalse(_setup(self._f(last_close=90, prev_high=91), p)[0])
        self.assertTrue(_setup(self._f(last_close=92, prev_high=91, depth_below_ma50_pct=8.0), p)[0])

    def test_market_filter(self):
        p = {"rsi_max": 30, "market_filter": True}
        good = {"spy_above_ma200": True, "vix": 25.0}
        self.assertTrue(_setup(self._f(), p, good)[0])
        self.assertFalse(_setup(self._f(), p, {"spy_above_ma200": False, "vix": 25.0})[0])
        self.assertFalse(_setup(self._f(), p, {"spy_above_ma200": True, "vix": 40.0})[0])
        self.assertFalse(_setup(self._f(), p, {"spy_above_ma200": True, "vix": 55.0})[0])

    def test_market_filter_with_no_market_data_fails_closed(self):
        ok, flags = _setup(self._f(), {"rsi_max": 30, "market_filter": True}, None)
        self.assertFalse(ok)
        self.assertFalse(flags["market_ok"])

    def test_market_filter_off_ignores_market_data(self):
        self.assertTrue(_setup(self._f(), {"rsi_max": 30, "market_filter": False}, None)[0])

    def test_thin_history_never_passes(self):
        nan = float("nan")
        self.assertFalse(_setup(self._f(ma50=nan, depth_below_ma50_pct=nan), {"rsi_max": 99})[0])
        self.assertFalse(_setup(self._f(rsi=nan), {"rsi_max": 99})[0])

    def test_every_flag_is_reported_so_a_rejection_is_explainable(self):
        _, flags = _setup(self._f(), {})
        self.assertEqual(set(flags), {"below_ma50", "oversold", "deep_enough", "uptrend_200",
                                      "stabilizing", "market_ok"})


class TestExitLevels(unittest.TestCase):
    F = {"atr": 2.0, "ma20": 98.0}

    def test_percent_stop(self):
        lv = _levels(100.0, self.F, {"stop_type": "pct", "stop_value": 0.07, "target_type": "none",
                                            "time_stop_sessions": 10})
        self.assertAlmostEqual(lv["stop"], 93.0)
        self.assertIsNone(lv["target"])
        self.assertEqual(lv["time_stop_sessions"], 10)

    def test_atr_stop(self):
        lv = _levels(100.0, self.F, {"stop_type": "atr", "stop_value": 2.0})
        self.assertAlmostEqual(lv["stop"], 96.0)

    def test_ma20_target_is_lifted_above_entry(self):
        lv = _levels(100.0, self.F, {"target_type": "ma20"})
        self.assertAlmostEqual(lv["target"], 101.0)
        lv = _levels(90.0, self.F, {"target_type": "ma20"})
        self.assertAlmostEqual(lv["target"], 98.0)

    def test_percent_target(self):
        lv = _levels(100.0, self.F, {"target_type": "pct", "target_value": 0.08})
        self.assertAlmostEqual(lv["target"], 108.0)

    def test_default_params_are_complete_and_valid(self):
        p = rs.DEFAULT_PARAMS
        self.assertEqual(set(p), {"rsi_max", "min_depth_pct", "require_uptrend_200", "stabilization",
                                  "market_filter", "stop_type", "stop_value", "target_type", "target_value",
                                  "time_stop_sessions"})
        self.assertIn(p["stabilization"], ("none", "close_rsi_up", "close_gt_prev_high"))
        self.assertIn(p["stop_type"], ("pct", "atr"))
        self.assertIn(p["target_type"], ("none", "ma20", "pct"))
        self.assertIn(p["time_stop_sessions"], (5, 10, 15))
        self.assertGreater(p["stop_value"], 0)


if __name__ == "__main__":
    unittest.main()
