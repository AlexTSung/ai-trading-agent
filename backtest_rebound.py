#!/usr/bin/env python3
"""
backtest_rebound.py — walk-forward backtest of the oversold-rebound rules in
rebound_signal.py against the current trend rule the Judge uses.

  python3 backtest_rebound.py --download-only   # fetch + cache prices
  python3 backtest_rebound.py                   # run on cached prices
  python3 backtest_rebound.py --refresh         # re-download everything, then run

Prices are keyless Yahoo daily bars, split- and dividend-adjusted, cached
OUTSIDE the repo. See the report for the caveats (survivorship bias in the
S&P 500 universe, multiple testing, one regime sample).
"""

import csv
import io
import json
import os
import sys
import tempfile
import time

import requests

import rebound_signal as rs

ROOT      = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.environ.get("REBOUND_PRICE_CACHE",
                           os.path.join(tempfile.gettempdir(), "rebound-price-cache"))
OUT_DIR   = os.path.join(ROOT, "logs", "backtest")
REPORT_DATE = "2026-11-09"

UA = {"User-Agent": "Mozilla/5.0"}
SP500_URL = ("https://raw.githubusercontent.com/datasets/s-and-p-500-companies/"
             "main/data/constituents.csv")
START_ORD = 730120  # 2000-01-01


def core_symbols():
    with open(os.path.join(ROOT, "core_universe.json")) as f:
        return list(json.load(f)["core"])


def sp500_symbols():
    r = requests.get(SP500_URL, headers=UA, timeout=30)
    r.raise_for_status()
    syms = [row["Symbol"].replace(".", "-") for row in csv.DictReader(io.StringIO(r.text)) if row.get("Symbol")]
    return sorted(set(syms))


# ── Download / cache ──────────────────────────────────────────────────────────
def _cache_path(sym):
    return os.path.join(CACHE_DIR, sym.replace("^", "_") + ".json")


def download(sym, refresh=False, tries=4):
    path = _cache_path(sym)
    if os.path.exists(path) and not refresh:
        return True
    # range=max silently downsamples long histories to coarser bars, so ask for
    # an explicit window (1998 onward gives warm-up before the 2001 first signal).
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
           f"?period1=883612800&period2={int(time.time())}&interval=1d&includeAdjustedClose=true")
    for k in range(tries):
        try:
            r = requests.get(url, headers=UA, timeout=30)
            if r.status_code == 404:
                return False
            r.raise_for_status()
            res = r.json()["chart"]["result"][0]
            off = res["meta"].get("gmtoffset", 0)
            q = res["indicators"]["quote"][0]
            adj = res["indicators"].get("adjclose", [{}])[0].get("adjclose")
            rows = []
            for i, ts in enumerate(res.get("timestamp") or []):
                o, h, l, c, v = (q[x][i] for x in ("open", "high", "low", "close", "volume"))
                a = adj[i] if adj else c
                if None in (o, h, l, c, a) or c <= 0 or a <= 0:
                    continue
                day = int((ts + off) // 86400) + 719163  # unix day -> proleptic ordinal
                k_ = a / c
                rows.append([day, o * k_, h * k_, l * k_, a, v or 0, c])
            if not rows:
                return False
            span = (rows[-1][0] - rows[0][0]) / 365.25
            if len(rows) < 150 * span:      # a daily series has ~252 bars a year
                raise ValueError(f"{sym}: {len(rows)} bars over {span:.1f}y is not daily data")
            os.makedirs(CACHE_DIR, exist_ok=True)
            with open(path, "w") as f:
                json.dump(rows, f)
            return True
        except Exception as e:  # noqa: BLE001 — network flake; retry then give up
            time.sleep(1.5 * (k + 1))
            last = e
    print(f"  [download] {sym} failed: {last}", file=sys.stderr)
    return False


def download_all(symbols, refresh=False, throttle=0.3):
    ok, bad = [], []
    for i, s in enumerate(symbols):
        had = os.path.exists(_cache_path(s)) and not refresh
        (ok if download(s, refresh) else bad).append(s)
        if not had:
            time.sleep(throttle)
        if (i + 1) % 50 == 0:
            print(f"  [download] {i + 1}/{len(symbols)} (failed {len(bad)})", file=sys.stderr, flush=True)
    return ok, bad


if __name__ == "__main__" and "--download-only" in sys.argv:
    syms = sorted(set(core_symbols() + sp500_symbols() + ["SPY", "^VIX"]))
    ok, bad = download_all(syms, refresh="--refresh" in sys.argv)
    print(f"downloaded/cached {len(ok)} of {len(syms)}; failed: {bad}")
    sys.exit(0)


# ═════════════════════════════════════════════════════════════════════════════
# Analysis
# ═════════════════════════════════════════════════════════════════════════════
import random
from datetime import date

import numpy as np

SIG_START = date(2001, 1, 1).toordinal()
CUTOFF    = date(2023, 1, 1).toordinal()          # selection uses data before this only
SLIP, COST = 0.001, 0.001
WIN = 15                                           # longest time stop
RSI_BASE = 35                                      # loosest rsi_max in the grid
MIN_N, MIN_DATES = 200, 60
BOOT, SEED = 2000, 20261109

STOPS   = [("pct", 0.05), ("pct", 0.07), ("pct", 0.10), ("atr", 2.0)]
TARGETS = [("none", 0.0), ("ma20", 0.0), ("pct", 0.08)]
TIMES   = [5, 10, 15]
EXITS   = [(s, t, m) for s in STOPS for t in TARGETS for m in TIMES]          # 36
BASELINE_EXIT = (("pct", 0.10), ("none", 0.0), 10)
RSI_GRID    = [25, 30, 35]
DEPTH_GRID  = [0.0, 8.0, 12.0]
STAB_GRID   = ["none", "close_rsi_up", "close_gt_prev_high"]
ENTRIES = [dict(rsi_max=r, min_depth_pct=d, require_uptrend_200=lt, stabilization=st, market_filter=mk)
           for r in RSI_GRID for d in DEPTH_GRID for lt in (False, True) for st in STAB_GRID for mk in (False, True)]


def exit_params(ex):
    (st, sv), (tt, tv), tm = ex
    return dict(stop_type=st, stop_value=sv, target_type=tt, target_value=tv, time_stop_sessions=tm)


def exit_label(ex):
    (st, sv), (tt, tv), tm = ex
    s = f"stop {sv:.0%}" if st == "pct" else f"stop {sv:g}xATR"
    t = {"none": "no target", "ma20": "target MA20", "pct": f"target +{tv:.0%}"}[tt]
    return f"{s}, {t}, {tm} sessions"


def entry_label(e):
    st = {"none": "no stabilization", "close_rsi_up": "close up & RSI turning up",
          "close_gt_prev_high": "close > prior high"}[e["stabilization"]]
    return (f"RSI<{e['rsi_max']}, >={e['min_depth_pct']:g}% below MA50, "
            f"{'above SMA200' if e['require_uptrend_200'] else 'any long-term trend'}, {st}, "
            f"{'market filter on' if e['market_filter'] else 'no market filter'}")


# ── Loading ───────────────────────────────────────────────────────────────────
def load_rows(sym):
    path = _cache_path(sym)
    if not os.path.exists(path) and not download(sym):
        return None
    with open(path) as f:
        a = np.array(json.load(f), dtype=np.float64)
    if a.ndim != 2 or len(a) < 260:
        return None
    a = a[np.argsort(a[:, 0], kind="stable")]
    a = a[np.concatenate(([True], np.diff(a[:, 0]) > 0))]
    ok = (a[:, 1] > 0) & (a[:, 2] > 0) & (a[:, 3] > 0) & (a[:, 4] > 0) & (a[:, 2] >= a[:, 3])
    a = a[ok]
    first = int(np.searchsorted(a[:, 0], START_ORD))
    a = a[max(first - 300, 0):]
    return a if len(a) >= 260 else None


class Market:
    def __init__(self):
        spy = load_rows("SPY")
        spy = spy[spy[:, 0] >= START_ORD]
        self.days = spy[:, 0].astype(np.int64)
        self.o, self.c = spy[:, 1], spy[:, 4]
        col = rs.feature_columns(*(spy[:, k].tolist() for k in (1, 2, 3, 4, 5)))
        ma50, ma200 = np.array(col["ma50"]), np.array(col["ma200"])
        with np.errstate(invalid="ignore"):
            self.above200 = self.c > ma200
            self.below50 = self.c < ma50
        vix = load_rows("^VIX")
        vm = np.full(len(self.days), np.nan)
        if vix is not None:
            ix = np.searchsorted(vix[:, 0], self.days, side="right") - 1
            good = ix >= 0
            vm[good] = vix[ix[good], 4]
        self.vix = vm
        with np.errstate(invalid="ignore"):
            self.mkt_ok = self.above200 & (self.vix < rs.VIX_MAX)

    def idx(self, day_arr):
        return np.clip(np.searchsorted(self.days, day_arr, side="right") - 1, 0, len(self.days) - 1)


# ── Event construction ────────────────────────────────────────────────────────
class Events:
    """Candidate signal events with every exit outcome precomputed."""
    FIELDS = ["sym", "ebar", "msig", "ment", "rsi", "depth", "lt", "stab1", "stab2", "mkt",
              "below50", "inA", "inB", "fill"]

    def __init__(self, exits):
        self.exits = exits
        self.parts = {k: [] for k in self.FIELDS + ["ret", "exc", "off", "mae", "mexit"]}
        self.n = 0

    def add(self, meta, out):
        for k in self.FIELDS:
            self.parts[k].append(meta[k])
        for k in ("ret", "exc", "off", "mae", "mexit"):
            self.parts[k].append(out[k])

    def finish(self):
        for k, v in self.parts.items():
            setattr(self, k, np.concatenate(v) if v else np.array([]))
        self.n = len(self.sym)
        self.parts = None
        return self


def simulate_events(O, H, L, C, ebar, atr_t, ma20_t, exits, mk, spy_o, spy_c):
    """Vectorised exit simulation matching rebound_signal.simulate_trade."""
    E = len(ebar)
    w = ebar[:, None] + np.arange(WIN)[None, :]
    Ow, Hw, Lw, Cw = O[w], H[w], L[w], C[w]
    fill = Ow[:, 0] * (1 + SLIP)
    day0 = np.arange(WIN)[None, :] > 0
    cumlow = np.minimum.accumulate(Lw, axis=1)
    rows = np.arange(E)
    K = len(exits)
    ret = np.empty((E, K), np.float32); exc = np.empty((E, K), np.float32)
    off = np.empty((E, K), np.int8); mae = np.empty((E, K), np.float32); mexit = np.empty((E, K), np.int16)
    cache = {}
    for k, ((st, sv), (tt, tv), tm) in enumerate(exits):
        key = (st, sv, tt, tv)
        if key not in cache:
            stop = fill * (1 - sv) if st == "pct" else fill - sv * atr_t
            if tt == "ma20":
                tgt = np.maximum(ma20_t, fill * rs.MIN_TARGET_ABOVE_ENTRY)
            elif tt == "pct":
                tgt = fill * (1 + tv)
            else:
                tgt = np.full(E, np.inf)
            s2, t2 = stop[:, None], tgt[:, None]
            gap_s = (Ow <= s2) & day0
            gap_t = (Ow >= t2) & day0 & ~gap_s
            hit_s = (Lw <= s2) & ~gap_s & ~gap_t
            hit_t = (Hw >= t2) & ~gap_s & ~gap_t & ~hit_s
            trig = gap_s | gap_t | hit_s | hit_t
            has, first = trig.any(1), trig.argmax(1)
            fs, ft = (gap_s | hit_s)[rows, first], (gap_t | hit_t)[rows, first]
            gap_any = (gap_s | gap_t)[rows, first]
            price = np.where(gap_any, Ow[rows, first], np.where(fs, stop, tgt))
            cache[key] = (has, first, price)
        has, first, price = cache[key]
        hit = has & (first <= tm - 1)
        o_ = np.where(hit, first, tm - 1)
        p_ = np.where(hit, price, Cw[:, tm - 1])
        r_ = p_ * (1 - COST) / (fill * (1 + COST)) - 1
        mx = np.clip(mk["mbar"][ebar + o_], 0, len(spy_c) - 1)
        spy_r = spy_c[mx] / spy_o[mk["ment"]] - 1
        ret[:, k], exc[:, k], off[:, k] = r_, r_ - spy_r, o_
        mae[:, k] = cumlow[rows, o_] / fill - 1
        mexit[:, k] = mx
    return dict(ret=ret, exc=exc, off=off, mae=mae, mexit=mexit, fill=fill.astype(np.float32))


def build(mkt, symbols_core, symbols_sp, exits_reb, exits_trd):
    core, sp = set(symbols_core), set(symbols_sp)
    reb, trd = Events(exits_reb), Events(exits_trd)
    universe = sorted(core | sp)
    checks = {"features": 0, "setup": 0, "trades": 0, "mismatch": 0}
    rng = random.Random(SEED)
    closes_m, sym_names = [], []
    unconditional = []
    t0 = time.time()
    for si, sym in enumerate(universe):
        a = load_rows(sym)
        if a is None:
            continue
        sid = len(sym_names)
        day = a[:, 0].astype(np.int64)
        O, H, L, C, V, RAW = a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5], a[:, 6]
        n = len(C)
        col = rs.feature_columns(O.tolist(), H.tolist(), L.tolist(), C.tolist(), V.tolist())
        ma20, ma50, ma200 = (np.array(col[k]) for k in ("ma20", "ma50", "ma200"))
        rsi, atr, dvol = (np.array(col[k]) for k in ("rsi", "atr", "dvol20"))
        mbar = mkt.idx(day)
        in_core, in_sp = sym in core, sym in sp
        with np.errstate(invalid="ignore", divide="ignore"):
            prevc, prevh = np.roll(C, 1), np.roll(H, 1)
            rsi_prev = np.roll(rsi, 1)
            depth = (ma50 - C) / ma50 * 100
            liquid = (RAW > 10) & (dvol > 2e7)
            inB = in_sp & liquid
            inA = np.full(n, in_core)
            member = inA | inB
            valid = (day >= SIG_START) & ~np.isnan(ma200) & ~np.isnan(rsi) & ~np.isnan(atr) & member
            base = valid & (C < ma50) & (rsi < RSI_BASE)
            trend = valid & (ma20 > ma50) & (C >= ma50) & (rsi <= rs.CHASE_RSI_MAX) \
                & ((C - ma20) / ma20 <= rs.CHASE_EXTENSION_MAX)
            lt = C > ma200
            stab1 = (C > prevc) & (rsi > rsi_prev)
            stab2 = C > prevh
        room = np.arange(n) + 1 + (WIN - 1) <= n - 1          # a full window after the entry bar
        # parity checks against the shared stdlib implementation
        if si % 40 == 0:
            bars = [dict(o=O[i], h=H[i], l=L[i], c=C[i], v=V[i]) for i in range(n)]
            for t in rng.sample(range(210, n - WIN - 2), 25):
                f = rs.features(bars[:t + 1])
                checks["features"] += 1
                for key, arr in (("ma20", ma20), ("ma50", ma50), ("ma200", ma200), ("rsi", rsi), ("atr", atr)):
                    if abs(f[key] - arr[t]) > 1e-7 * max(1, abs(arr[t])):
                        checks["mismatch"] += 1
                ok_s, _ = rs.setup(f, dict(rsi_max=RSI_BASE, min_depth_pct=0.0, require_uptrend_200=False,
                                           stabilization="none", market_filter=False))
                checks["setup"] += 1
                if ok_s != bool((C[t] < ma50[t]) and (rsi[t] < RSI_BASE)):
                    checks["mismatch"] += 1
                if base[t] and room[t]:
                    for ex in rng.sample(EXITS, 6):
                        res = rs.simulate_trade(bars[t + 1:t + 1 + WIN], f, exit_params(ex), SLIP, COST)
                        checks["trades"] += 1
                        mm = dict(ment=mbar[[t + 1]], mbar=mbar)
                        sim = simulate_events(O, H, L, C, np.array([t + 1]), atr[[t]], ma20[[t]], [ex], mm,
                                              mkt.o, mkt.c)
                        if abs(sim["ret"][0, 0] - res["ret"]) > 1e-5 or sim["off"][0, 0] != res["exit_offset"] \
                                or abs(sim["mae"][0, 0] - res["mae"]) > 1e-5:
                            checks["mismatch"] += 1
        # unconditional 10-session excess vs SPY (survivorship drift estimate), sampled every 5th day
        if in_sp:
            ts = np.nonzero(valid & inB & room & (np.arange(n) % 5 == 0))[0]
            if len(ts):
                me, mx = mbar[ts + 1], np.clip(mbar[ts + 10], 0, len(mkt.c) - 1)
                unconditional.append(np.mean(C[ts + 10] / O[ts + 1] - 1 - (mkt.c[mx] / mkt.o[me] - 1)))
        for kind, mask, store, exits in (("reb", base, reb, exits_reb), ("trd", trend, trd, exits_trd)):
            ts = np.nonzero(mask & room)[0]
            if not len(ts):
                continue
            eb = ts + 1
            mm = dict(ment=mbar[eb], mbar=mbar)
            out = simulate_events(O, H, L, C, eb, atr[ts], ma20[ts], exits, mm, mkt.o, mkt.c)
            meta = dict(sym=np.full(len(ts), sid, np.int32), ebar=eb.astype(np.int32), msig=mbar[ts].astype(np.int32),
                        ment=mbar[eb].astype(np.int32), rsi=rsi[ts], depth=depth[ts], lt=lt[ts], stab1=stab1[ts],
                        stab2=stab2[ts], mkt=mkt.mkt_ok[mbar[ts]], below50=mkt.below50[mbar[ts]],
                        inA=inA[ts], inB=inB[ts], fill=out["fill"])
            store.add(meta, out)
        cm = np.full(len(mkt.days), np.nan, np.float32)
        ix = np.searchsorted(day, mkt.days, side="right") - 1
        good = (ix >= 0) & (mkt.days >= day[0])
        cm[good] = C[ix[good]]
        closes_m.append(cm)
        sym_names.append(sym)
        if (si + 1) % 100 == 0:
            print(f"  [build] {si + 1}/{len(universe)} symbols, {time.time() - t0:.0f}s", file=sys.stderr, flush=True)
    reb.finish(); trd.finish()
    return reb, trd, np.array(closes_m), sym_names, checks, float(np.mean(unconditional)) if unconditional else 0.0


# ── Statistics ────────────────────────────────────────────────────────────────
def _cluster(x, dates):
    _, inv = np.unique(dates, return_inverse=True)
    return np.bincount(inv, weights=x), np.bincount(inv).astype(float)


def cluster_mean_se(x, dates):
    """Mean and its standard error with trades clustered by signal date, so
    many names signalling on one day count as one observation, not many."""
    S, N = _cluster(x, dates)
    m = S.sum() / N.sum()
    D = len(N)
    var = ((S - m * N) ** 2).sum() * D / max(D - 1, 1) / N.sum() ** 2
    return m, var ** 0.5, D


def boot_ci(x, dates, seed=SEED):
    S, N = _cluster(x, dates)
    D = len(N)
    idx = np.random.default_rng(seed).integers(0, D, size=(BOOT, D))
    means = S[idx].sum(1) / N[idx].sum(1)
    return float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))


def trade_stats(ev, idx, k, ci=True):
    if len(idx) == 0:
        return {"n": 0}
    r = ev.ret[idx, k].astype(np.float64)
    e = ev.exc[idx, k].astype(np.float64)
    d = ev.msig[idx]
    pos, neg = r[r > 0].sum(), -r[r <= 0].sum()
    out = {"n": int(len(idx)), "n_dates": int(len(np.unique(d))),
           "win_rate": float((r > 0).mean()), "mean_ret": float(r.mean()), "median_ret": float(np.median(r)),
           "profit_factor": float(pos / neg) if neg > 0 else float("inf"),
           "avg_hold": float((ev.off[idx, k].astype(float) + 1).mean()),
           "avg_mae": float(ev.mae[idx, k].mean()),
           "mean_excess": float(e.mean()), "median_excess": float(np.median(e)),
           "beat_spy": float((e > 0).mean())}
    if ci and out["n_dates"] > 3:
        out["ci_ret"] = boot_ci(r, d)
        out["ci_excess"] = boot_ci(e, d)
    return out


class Selector:
    def __init__(self, ev, mkt):
        self.ev, self.mkt = ev, mkt
        self.sym_l, self.e_l = ev.sym.tolist(), ev.ebar.tolist()
        self.off_l = {}
        self.ent_day = mkt.days[ev.ment]
        self.exit_day = mkt.days[ev.mexit.astype(np.int64)]

    def entry_mask(self, cfg, uni):
        ev = self.ev
        m = ev.rsi < cfg["rsi_max"]
        m &= ev.depth >= cfg["min_depth_pct"]
        if cfg["require_uptrend_200"]:
            m &= ev.lt
        if cfg["stabilization"] == "close_rsi_up":
            m &= ev.stab1
        elif cfg["stabilization"] == "close_gt_prev_high":
            m &= ev.stab2
        if cfg["market_filter"]:
            m &= ev.mkt
        return m & (ev.inB if uni == "B" else ev.inA)

    def period(self, name, k):
        if name == "in":
            return (self.ent_day < CUTOFF) & (self.exit_day[:, k] < CUTOFF)
        if name == "out":
            return self.ent_day >= CUTOFF
        return np.ones(self.ev.n, bool)

    def non_overlap(self, mask, k):
        """One open position per symbol, greedy by entry date."""
        idx = np.nonzero(mask)[0]
        if len(idx) < 2:
            return idx
        s, e = self.ev.sym[idx], self.ev.ebar[idx]
        if not np.any((s[1:] == s[:-1]) & (np.diff(e) < WIN)):
            return idx
        if k not in self.off_l:
            self.off_l[k] = self.ev.off[:, k].tolist()
        off, sl, el = self.off_l[k], self.sym_l, self.e_l
        keep, last, free = [], -1, -1
        for i in idx.tolist():
            if sl[i] != last:
                last, free = sl[i], -1
            if el[i] > free:
                keep.append(i)
                free = el[i] + off[i]
        return np.array(keep, dtype=np.int64)

    def trades(self, cfg, k, uni, period):
        return self.non_overlap(self.entry_mask(cfg, uni) & self.period(period, k), k)


def grid_search(sel, log):
    rows = []
    t0 = time.time()
    for ci, cfg in enumerate(ENTRIES):
        base = sel.entry_mask(cfg, "B") & (sel.ent_day < CUTOFF)
        if base.sum() < MIN_N:
            continue
        for k in range(len(EXITS)):
            idx = sel.non_overlap(base & (sel.exit_day[:, k] < CUTOFF), k)
            if len(idx) < MIN_N:
                continue
            exc = sel.ev.exc[idx, k].astype(np.float64)
            m, se, D = cluster_mean_se(exc, sel.ev.msig[idx])
            if D < MIN_DATES:
                continue
            ret = sel.ev.ret[idx, k].astype(np.float64)
            rows.append({"ci": ci, "k": k, "n": int(len(idx)), "dates": int(D), "mean_excess": float(m),
                         "se": float(se), "lcb": float(m - se), "mean_ret": float(ret.mean()),
                         "win_rate": float((ret > 0).mean())})
        if (ci + 1) % 12 == 0:
            log(f"  [grid] {ci + 1}/{len(ENTRIES)} entry configs, {time.time() - t0:.0f}s")
    rows.sort(key=lambda r: r["lcb"], reverse=True)
    return rows


# ── Portfolio simulation ──────────────────────────────────────────────────────
def portfolio_sim(ev, mask, k, closes, mkt, start_idx, label, max_pos=5, size=0.05, equity0=100_000.0):
    idx = np.nonzero(mask & (ev.ment >= start_idx))[0]
    order = idx[np.argsort(ev.ment[idx], kind="stable")]
    ment = ev.ment[order]
    T = len(mkt.days)
    starts = np.searchsorted(ment, np.arange(T + 1))
    cash, eq_prev = equity0, equity0
    pos, curve, expo, trades = [], np.full(T, np.nan), np.zeros(T), 0
    for d in range(start_idx, T):
        lo, hi = starts[d], starts[d + 1]
        if hi > lo and len(pos) < max_pos:
            held = {p["sym"] for p in pos}
            cand = sorted(order[lo:hi].tolist(), key=lambda i: ev.rsi[i])
            for i in cand:
                if len(pos) >= max_pos:
                    break
                if int(ev.sym[i]) in held:
                    continue
                alloc = size * eq_prev
                if alloc > cash:
                    continue
                cash -= alloc
                pos.append({"sym": int(ev.sym[i]), "shares": alloc / (float(ev.fill[i]) * (1 + COST)),
                            "exit": int(ev.mexit[i, k]), "proceeds": alloc * (1 + float(ev.ret[i, k]))})
                held.add(int(ev.sym[i]))
                trades += 1
        still = []
        for p in pos:
            if p["exit"] <= d:
                cash += p["proceeds"]
            else:
                still.append(p)
        pos = still
        mtm = 0.0
        for p in pos:
            px = closes[p["sym"], d]
            mtm += p["shares"] * (px if px == px else 0.0)
        eq = cash + mtm
        curve[d], eq_prev, expo[d] = eq, eq, (mtm / eq if eq else 0.0)
    v = curve[start_idx:]
    span = (mkt.days[-1] - mkt.days[start_idx]) / 365.25
    peak = np.maximum.accumulate(v)
    return {"label": label, "trades": trades, "end_equity": float(v[-1]),
            "cagr": float((v[-1] / equity0) ** (1 / span) - 1), "max_drawdown": float(((v - peak) / peak).min()),
            "exposure": float(expo[start_idx:].mean())}


def spy_hold(mkt, start_idx):
    c = mkt.c[start_idx:]
    span = (mkt.days[-1] - mkt.days[start_idx]) / 365.25
    peak = np.maximum.accumulate(c)
    return {"label": "SPY buy & hold", "trades": 0, "end_equity": float(100_000 * c[-1] / c[0]),
            "cagr": float((c[-1] / c[0]) ** (1 / span) - 1), "max_drawdown": float(((c - peak) / peak).min()),
            "exposure": 1.0}


# ── Report ────────────────────────────────────────────────────────────────────
def pct(x, d=2, sign=False):
    if x is None or x != x:
        return "-"
    return f"{x * 100:+.{d}f}%" if sign else f"{x * 100:.{d}f}%"


def stats_row(label, s):
    if not s.get("n"):
        return f"| {label} | 0 | | | | | | | | |"
    ci = s.get("ci_excess")
    return (f"| {label} | {s['n']} | {pct(s['win_rate'], 0)} | {pct(s['mean_ret'], sign=True)} | "
            f"{pct(s['median_ret'], sign=True)} | {s['profit_factor']:.2f} | {s['avg_hold']:.1f} | "
            f"{pct(s['avg_mae'], 1)} | {pct(s['mean_excess'], sign=True)} | "
            f"{('[' + pct(ci[0], sign=True) + ', ' + pct(ci[1], sign=True) + ']') if ci else '-'} |")


HEAD = ("| set | n | win | mean | median | PF | hold | avg MAE | mean vs SPY | 95% CI vs SPY (date-clustered) |\n"
        "|---|---|---|---|---|---|---|---|---|---|")

SPECIAL = [("GFC (Sep 2008 - Mar 2009)", date(2008, 9, 1), date(2009, 3, 31)),
           ("Aug - Dec 2011", date(2011, 8, 1), date(2011, 12, 31)),
           ("2018 Q4", date(2018, 10, 1), date(2018, 12, 31)),
           ("Feb - Jun 2020", date(2020, 2, 15), date(2020, 6, 30)),
           ("2022", date(2022, 1, 1), date(2022, 12, 31)),
           ("Mar - Jun 2025", date(2025, 3, 1), date(2025, 6, 30))]


def render(R):
    L = []
    w = L.append
    m = R["meta"]
    w(f"# Oversold-rebound backtest — {REPORT_DATE}\n")
    w("Selection uses data **before 2023-01-01 only**; the chosen config is then judged once on the untouched "
      "holdout 2023-01-01 to the latest data. All returns are net of 10 bps slippage on entry plus 10 bps cost per "
      "side. Signals use the close of day t, entry is the open of t+1, exits use daily highs/lows (stop first "
      "when both are touched; gaps through the stop fill at the open). One open position per symbol.\n")
    w("## Data and caveats that matter\n")
    w(f"- Keyless Yahoo daily bars, split- and dividend-adjusted. {m['symbols']} symbols loaded "
      f"({m['sp_symbols']} S&P 500 members as of today, {m['core_symbols']} core names), 2001 to {m['last_date']}.")
    w("- **Universe B (S&P 500) has survivorship bias**: it holds only today's members, i.e. companies that did "
      "not fail or get dropped. Any buy-the-dip rule is flattered by this. A rough size of the drift: a random "
      f"10-session entry in B beat SPY by {pct(m['uncond_excess'], 2, True)} per trade on average. "
      "Read B's excess returns net of that.")
    w("- **Universe A (the 23 core names) is hindsight-picked**: the list was chosen in Nov 2026, "
      "and several names (HOOD, RKLB, ASTS, GEV) have only a few years of history.")
    w(f"- **Multiple testing**: {m['configs']} configurations were searched ({m['entry_cfgs']} entry x {m['exit_cfgs']} "
      f"exit); {m['kept']} met the minimum sample ({MIN_N} trades, {MIN_DATES} signal dates) in-sample. The best of that "
      "many is expected to look good by chance; the holdout is the only honest number.")
    w("- Windows overlap and signals cluster in selloffs, so the effective sample is far smaller than n. "
      "Confidence intervals resample **signal dates**, not trades.")
    w(f"- Events in the last {WIN} sessions are dropped (their windows are incomplete), so the most recent "
      "weeks are not in the sample. Yahoo adjusted bars are not point-in-time clean; $10 / $20M liquidity "
      "filters use split-adjusted values.")
    w(f"- Implementation parity: {m['checks']['features']} indicator, {m['checks']['setup']} setup and "
      f"{m['checks']['trades']} trade checks of the vectorised backtest against the stdlib live code, "
      f"{m['checks']['mismatch']} mismatches.\n")

    w("## Selection (in-sample, universe B, before 2023)\n")
    w("Ranked by the lower bound (mean excess return vs SPY minus one clustered standard error).\n")
    w("| rank | entry rule | exit rule | n | mean vs SPY | LCB | mean ret | win |\n|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(R["top"]):
        w(f"| {i + 1} | {r['entry']} | {r['exit']} | {r['n']} | {pct(r['mean_excess'], sign=True)} | "
          f"{pct(r['lcb'], sign=True)} | {pct(r['mean_ret'], sign=True)} | {pct(r['win_rate'], 0)} |")
    d = R["decay"]
    w(f"\nOf the {m['kept']} qualifying configs, {pct(m['frac_pos'], 0)} had a positive in-sample LCB. "
      f"Selection decay: the in-sample top {d['n']} had a median holdout excess return of "
      f"{pct(d['median_holdout'], 2, True)} (in-sample median {pct(d['median_insample'], 2, True)}); "
      f"{d['positive']} of {d['n']} were positive on the holdout.\n")
    c = R["chosen"]
    w(f"**Chosen:** {c['entry']}; {c['exit']}.\n")
    w(f"**Runner-up (different entry rule):** {R['second']['entry']}; {R['second']['exit']}.\n")

    w("## Chosen config vs naive rebound vs the current trend rule\n")
    for uni, nm in (("B", "Universe B: S&P 500 today (survivorship-biased)"), ("A", "Universe A: the 23 core names")):
        w(f"### {nm}\n")
        w(HEAD)
        for per, pn in (("in", "in-sample < 2023"), ("out", "HOLDOUT 2023+")):
            for key, lab in (("chosen", "Chosen rebound"), ("second", "Runner-up rebound"),
                             ("naive_chosen_exit", "Naive RSI<30 (chosen exits)"),
                             ("naive_base_exit", "Naive RSI<30 (stop 10%, 10 sessions)"),
                             ("trend", "Current trend rule (stop 10%, 10 sessions)")):
                w(stats_row(f"{lab}, {pn}", R["sets"][uni][per][key]))
        w("")

    w("## When SPY is below its 50-day average (today's regime), and 2022\n")
    for uni in ("B", "A"):
        w(f"### Universe {uni}, chosen config, all years\n")
        w(HEAD)
        for lab, key in (("SPY below SMA50 at signal", "below"), ("SPY above SMA50 at signal", "above")):
            w(stats_row(lab, R["regime"][uni]["chosen"][key]))
        for lab, key in (("SPY below SMA50 at signal (trend rule)", "below"), ("SPY above SMA50 (trend rule)", "above")):
            w(stats_row(lab, R["regime"][uni]["trend"][key]))
        for nm, s in R["special"][uni]["chosen"].items():
            w(stats_row(f"{nm}", s))
        for nm, s in R["special"][uni]["trend"].items():
            w(stats_row(f"{nm} (trend rule)", s))
        w("")

    w("## By year, chosen config\n")
    w("| year | B n | B mean | B median | B vs SPY | A n | A mean | A median | A vs SPY |\n|---|---|---|---|---|---|---|---|---|")
    for y in sorted(R["years"]["B"]):
        b, a = R["years"]["B"].get(y, {}), R["years"]["A"].get(y, {})
        fb = lambda s, k, sg=True: pct(s[k], 2, sg) if s.get("n") else "-"
        w(f"| {y} | {b.get('n', 0)} | {fb(b, 'mean_ret')} | {fb(b, 'median_ret')} | {fb(b, 'mean_excess')} | "
          f"{a.get('n', 0)} | {fb(a, 'mean_ret')} | {fb(a, 'median_ret')} | {fb(a, 'mean_excess')} |")

    w("\n## Portfolio simulation\n")
    w("Max 5 concurrent positions, 5% of current equity each, lowest RSI first, $100k start, "
      "a position's slot is freed the day after it exits.\n")
    w("| universe | period | strategy | CAGR | max drawdown | exposure | trades | end equity |\n|---|---|---|---|---|---|---|---|")
    for uni in ("B", "A"):
        for per in ("full", "holdout"):
            for s in R["sim"][uni][per]:
                w(f"| {uni} | {'2001-' if per == 'full' else '2023-'} | {s['label']} | {pct(s['cagr'], 1, True)} | "
                  f"{pct(s['max_drawdown'], 1)} | {pct(s['exposure'], 0)} | {s['trades']} | ${s['end_equity']:,.0f} |")
    return "\n".join(L) + "\n"


# ── Driver ────────────────────────────────────────────────────────────────────
def run(refresh=False):
    log = lambda msg: print(msg, file=sys.stderr, flush=True)
    core, sp = core_symbols(), sp500_symbols()
    if refresh:
        download_all(sorted(set(core + sp + ["SPY", "^VIX"])), refresh=True)
    mkt = Market()
    reb, trd, closes, names, checks, uncond = build(mkt, core, sp, EXITS, [BASELINE_EXIT])
    log(f"events: rebound {reb.n:,}, trend {trd.n:,}; symbols {len(names)}; parity {checks}")
    if checks["mismatch"]:
        raise SystemExit(f"parity check failed: {checks}")
    selR, selT = Selector(reb, mkt), Selector(trd, mkt)

    rows = grid_search(selR, log)
    if not rows:
        raise SystemExit("no configuration met the minimum sample")
    best = rows[0]
    second = next(r for r in rows if r["ci"] != best["ci"])
    label = lambda r: {"entry": entry_label(ENTRIES[r["ci"]]), "exit": exit_label(EXITS[r["k"]])}
    top = [{**r, **label(r)} for r in rows[:10]]

    # selection decay: holdout result of the in-sample top configs
    hold, ins = [], []
    for r in rows[:50]:
        idx = selR.trades(ENTRIES[r["ci"]], r["k"], "B", "out")
        if len(idx) >= 30:
            hold.append(float(reb.exc[idx, r["k"]].astype(float).mean()))
            ins.append(r["mean_excess"])
    decay = {"n": len(hold), "median_holdout": float(np.median(hold)) if hold else None,
             "median_insample": float(np.median(ins)) if ins else None, "positive": int(sum(h > 0 for h in hold))}

    cfg_c, k_c = ENTRIES[best["ci"]], best["k"]
    cfg_2, k_2 = ENTRIES[second["ci"]], second["k"]
    naive = dict(rsi_max=30, min_depth_pct=0.0, require_uptrend_200=False, stabilization="none", market_filter=False)
    k_base = EXITS.index(BASELINE_EXIT)

    def trend_idx(uni, per):
        m = (trd.inB if uni == "B" else trd.inA) & selT.period(per, 0)
        return selT.non_overlap(m, 0)

    sets = {}
    for uni in ("B", "A"):
        sets[uni] = {}
        for per in ("in", "out"):
            sets[uni][per] = {
                "chosen": trade_stats(reb, selR.trades(cfg_c, k_c, uni, per), k_c),
                "second": trade_stats(reb, selR.trades(cfg_2, k_2, uni, per), k_2),
                "naive_chosen_exit": trade_stats(reb, selR.trades(naive, k_c, uni, per), k_c),
                "naive_base_exit": trade_stats(reb, selR.trades(naive, k_base, uni, per), k_base),
                "trend": trade_stats(trd, trend_idx(uni, per), 0)}

    years_m = np.array([date.fromordinal(int(d)).year for d in mkt.days])
    regime, special, years = {}, {}, {}
    for uni in ("B", "A"):
        ch, tr = selR.trades(cfg_c, k_c, uni, "all"), trend_idx(uni, "all")
        regime[uni], special[uni] = {}, {}
        for nm, ev, idx, k in (("chosen", reb, ch, k_c), ("trend", trd, tr, 0)):
            below = ev.below50[idx]
            regime[uni][nm] = {"below": trade_stats(ev, idx[below], k), "above": trade_stats(ev, idx[~below], k)}
            sp_ = {}
            ed = mkt.days[ev.ment[idx]]
            for pn, a, b in SPECIAL:
                sel_ = (ed >= a.toordinal()) & (ed <= b.toordinal())
                sp_[pn] = trade_stats(ev, idx[sel_], k, ci=False)
            special[uni][nm] = sp_
        yr = years_m[reb.ment[ch]]
        years[uni] = {int(y): trade_stats(reb, ch[yr == y], k_c, ci=False) for y in np.unique(yr)}

    sim = {}
    for uni in ("B", "A"):
        sim[uni] = {}
        em = selR.entry_mask(cfg_c, uni)
        tm = (trd.inB if uni == "B" else trd.inA)
        for per, start in (("full", int(np.searchsorted(mkt.days, SIG_START))),
                           ("holdout", int(np.searchsorted(mkt.days, CUTOFF)))):
            sim[uni][per] = [portfolio_sim(reb, em, k_c, closes, mkt, start, "Chosen rebound"),
                             portfolio_sim(trd, tm, 0, closes, mkt, start, "Current trend rule"),
                             spy_hold(mkt, start)]

    meta = {"symbols": len(names), "sp_symbols": len(sp), "core_symbols": len(core),
            "last_date": date.fromordinal(int(mkt.days[-1])).isoformat(), "configs": len(ENTRIES) * len(EXITS),
            "entry_cfgs": len(ENTRIES), "exit_cfgs": len(EXITS), "kept": len(rows),
            "frac_pos": float(np.mean([r["lcb"] > 0 for r in rows])), "uncond_excess": uncond,
            "checks": checks, "events_rebound": int(reb.n), "events_trend": int(trd.n)}
    chosen_params = {**cfg_c, **exit_params(EXITS[k_c])}
    R = {"meta": meta, "top": top, "decay": decay, "chosen": {**label(best), **best},
         "second": {**label(second), **second}, "chosen_params": chosen_params,
         "sets": sets, "regime": regime, "special": special, "years": years, "sim": sim}
    os.makedirs(OUT_DIR, exist_ok=True)
    base = os.path.join(OUT_DIR, f"rebound-backtest-{REPORT_DATE}")
    with open(base + ".json", "w") as f:
        json.dump(R, f, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    with open(base + ".md", "w") as f:
        f.write(render(R))
    log(f"wrote {base}.json / .md")
    return R


if __name__ == "__main__":
    run(refresh="--refresh" in sys.argv)
