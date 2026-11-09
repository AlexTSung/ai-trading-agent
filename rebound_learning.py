#!/usr/bin/env python3
"""
rebound_learning.py — the recursive loop for the oversold-rebound path.

    scan (rebound.py)  ->  shadow ledger  ->  resolve outcomes  ->  learn (here)
         ^                                                              |
         +------------ logs/learnings/rebound_params.json <-------------+

The ledger holds every setup the wide net saw, not just the few that traded, so
there is far more to learn from than paper fills alone. Each day this module
slices the resolved rows by the things the rules decide (how oversold, how far
below the 50-day, stabilizing or not, above the 200-day or not, market regime,
symbol) and asks: is any slice the rules still allow clearly losing money?

Rails, the same shape as judge.py's learned hints:
  * TIGHTEN-ONLY, AUTOMATIC. It may make the rules stricter (lower RSI ceiling,
    deeper required drop, require stabilization / uptrend / a healthy market,
    block a symbol, shrink size or slots). rebound.tighten_only() re-enforces
    this when the file is loaded, so an edited file cannot widen anything.
  * LOOSENING IS A PROPOSAL, NEVER APPLIED. Loosening a documented floor is a
    MAJOR change (CLAUDE.md versioning), so it goes to
    logs/proposals/rebound-proposals.json for Alex.
  * BROAD EVIDENCE, NARROW ACTION. Parameter slices are learned from every tier
    in the ledger (core, speculative, probation, screener universe), because
    that is where the volume is; the rules only ever act on core names. The
    circuit breaker alone looks at core names only.
  * SAMPLE. A slice needs MIN_BUCKET_N resolved rows (and a comparison group)
    before it can move anything, so one bad week cannot rewrite the rules.
  * RE-DERIVED EACH RUN from the full ledger, so a slice that recovers is
    released again; nothing is permanent except what the evidence supports now.

Usage:
  python3 rebound_learning.py learn
"""

import json
import os
import statistics
import sys
from datetime import date

import rebound
from market_time import et_today_iso
from version import get_version

ROOT      = os.path.dirname(os.path.abspath(__file__))
PROPOSALS = os.path.join(ROOT, "logs", "proposals", "rebound-proposals.json")

MIN_BUCKET_N   = 20      # resolved rows in a slice before it can tighten anything
MIN_REST_N     = 10      # rows in the rest, so we compare rather than just react
SYMBOL_MIN_N   = 8
BREAKER_WINDOW = 30
LOOSEN_MIN_N   = 30
LOOSEN_MIN_MEDIAN = 0.01
LOOSEN_MIN_WIN    = 0.55
BAD_MEDIAN, BAD_WIN = 0.0, 0.45


# ── Row helpers ───────────────────────────────────────────────────────────────
def _f(x):
    try:
        return float(x) if x not in ("", None) else None
    except ValueError:
        return None


def parse(row: dict) -> dict:
    return {
        "symbol": row["symbol"], "date": row["signal_date"], "tier": row["tier"],
        "rsi": _f(row["rsi"]), "depth": _f(row["depth_pct"]),
        "above_ma200": row["above_ma200"] == "1", "stab_rsi_up": row["stab_rsi_up"] == "1",
        "stab_prev_high": row["stab_prev_high"] == "1",
        "spy_ok": row["spy_above_ma200"] == "1", "would_trade": row["would_trade"] == "1",
        "ret": _f(row["sim_ret"]),
        "excess": (_f(row["r10"]) - _f(row["spy_r10"])) if _f(row["r10"]) is not None and _f(row["spy_r10"]) is not None else None,
    }


def resolved(rows: list) -> list:
    return [p for p in map(parse, rows) if p["ret"] is not None and p["rsi"] is not None]


def stats(rows: list) -> dict:
    rets = [r["ret"] for r in rows]
    ex = [r["excess"] for r in rows if r["excess"] is not None]
    n = len(rets)
    return {"n": n, "win": (sum(x > 0 for x in rets) / n) if n else None,
            "median": statistics.median(rets) if n else None,
            "mean": statistics.mean(rets) if n else None,
            "excess": statistics.mean(ex) if ex else None}


def is_bad(s: dict) -> bool:
    return s["n"] >= MIN_BUCKET_N and s["median"] < BAD_MEDIAN and s["win"] < BAD_WIN


def is_good(s: dict) -> bool:
    return (s["n"] >= LOOSEN_MIN_N and s["median"] > LOOSEN_MIN_MEDIAN and s["win"] >= LOOSEN_MIN_WIN
            and (s["excess"] is None or s["excess"] > 0))


def passes(r: dict, p: dict, skip: str = None) -> bool:
    """Would this row pass the CURRENT trading rules, ignoring one dimension?"""
    checks = {
        "rsi":   r["rsi"] < p["rsi_max"],
        "depth": r["depth"] >= p["min_depth_pct"],
        "stab":  (p["stabilization"] == "none"
                  or (p["stabilization"] == "close_rsi_up" and r["stab_rsi_up"])
                  or (p["stabilization"] == "close_gt_prev_high" and r["stab_prev_high"])),
        "trend": (not p["require_uptrend_200"]) or r["above_ma200"],
        "market": (not p["market_filter"]) or r["spy_ok"],
        "symbol": r["symbol"] not in set(p.get("blocked_symbols", [])),
    }
    return all(v for k, v in checks.items() if k != skip)


# ── Tighten ───────────────────────────────────────────────────────────────────
def derive(rows: list, base: dict) -> tuple:
    """Return (learned_params, evidence) from the resolved ledger rows."""
    data = resolved(rows)
    learned, evidence = {}, []
    cur = dict(base)

    def note(msg, s):
        evidence.append({"change": msg, "n": s["n"], "win": s["win"], "median": s["median"]})

    # RSI ceiling: is the part just under the ceiling losing money?
    for t in range(int(cur["rsi_max"]) - 5, 14, -5):
        bucket = [r for r in data if passes(r, cur, "rsi") and t <= r["rsi"] < cur["rsi_max"]]
        rest = [r for r in data if passes(r, cur, "rsi") and r["rsi"] < t]
        sb, sr = stats(bucket), stats(rest)
        if is_bad(sb) and sr["n"] >= MIN_REST_N and sr["median"] > sb["median"]:
            learned["rsi_max"] = cur["rsi_max"] = t
            note(f"rsi_max -> {t} (RSI {t}-{int(base['rsi_max'])} loses money)", sb)
            break

    # Depth below the 50-day: shallow dips that lose.
    for d in (4, 8, 12):
        if d <= cur["min_depth_pct"]:
            continue
        bucket = [r for r in data if passes(r, cur, "depth") and cur["min_depth_pct"] <= r["depth"] < d]
        rest = [r for r in data if passes(r, cur, "depth") and r["depth"] >= d]
        sb, sr = stats(bucket), stats(rest)
        if is_bad(sb) and sr["n"] >= MIN_REST_N and sr["median"] > sb["median"]:
            learned["min_depth_pct"] = cur["min_depth_pct"] = d
            note(f"min_depth_pct -> {d} (dips shallower than {d}% lose money)", sb)

    if cur["stabilization"] == "none":
        bucket = [r for r in data if passes(r, cur, "stab") and not r["stab_rsi_up"]]
        rest = [r for r in data if passes(r, cur, "stab") and r["stab_rsi_up"]]
        sb, sr = stats(bucket), stats(rest)
        if is_bad(sb) and sr["n"] >= MIN_REST_N and sr["median"] > sb["median"]:
            learned["stabilization"] = cur["stabilization"] = "close_rsi_up"
            note("require stabilization (setups still falling lose money)", sb)

    if not cur["require_uptrend_200"]:
        bucket = [r for r in data if passes(r, cur, "trend") and not r["above_ma200"]]
        rest = [r for r in data if passes(r, cur, "trend") and r["above_ma200"]]
        sb, sr = stats(bucket), stats(rest)
        if is_bad(sb) and sr["n"] >= MIN_REST_N and sr["median"] > sb["median"]:
            learned["require_uptrend_200"] = cur["require_uptrend_200"] = True
            note("require price above the 200-day (long-term downtrends lose money)", sb)

    if not cur["market_filter"]:
        bucket = [r for r in data if passes(r, cur, "market") and not r["spy_ok"]]
        rest = [r for r in data if passes(r, cur, "market") and r["spy_ok"]]
        sb, sr = stats(bucket), stats(rest)
        if is_bad(sb) and sr["n"] >= MIN_REST_N and sr["median"] > sb["median"]:
            learned["market_filter"] = cur["market_filter"] = True
            note("require SPY above its 200-day and VIX below the cap (weak markets lose money)", sb)

    blocked = set(cur.get("blocked_symbols", []))
    for sym in sorted({r["symbol"] for r in data}):
        mine = [r for r in data if r["symbol"] == sym and passes(r, cur, "symbol")]
        s = stats(mine)
        if s["n"] >= SYMBOL_MIN_N and s["win"] <= 0.25 and s["mean"] < 0:
            blocked.add(sym)
            note(f"block {sym} (win rate {s['win']:.0%} over {s['n']})", s)
    if blocked - set(base.get("blocked_symbols", [])):
        learned["blocked_symbols"] = sorted(blocked)

    # Circuit breaker on the whole sleeve: the recent rows the rules DID allow.
    allowed = sorted((r for r in data if r["tier"] == "core" and passes(r, cur)),
                     key=lambda r: r["date"])[-BREAKER_WINDOW:]
    s = stats(allowed)
    if s["n"] >= BREAKER_WINDOW and s["mean"] < 0 and s["win"] < 0.40:
        learned["position_pct"] = max(rebound.FLOORS["position_pct"], base["position_pct"] / 2)
        learned["max_open"] = max(rebound.FLOORS["max_open"], base["max_open"] - 1)
        note("circuit breaker: halve size and drop a slot (last "
             f"{BREAKER_WINDOW} allowed setups lose money)", s)
    return learned, evidence


# ── Loosen (proposals only) ───────────────────────────────────────────────────
def propose(rows: list, cur: dict, base: dict) -> list:
    """Slices the current rules exclude that look good enough to be worth Alex
    loosening for. Nothing here is ever applied."""
    data = resolved(rows)
    out = []

    def add(change, bucket):
        s = stats(bucket)
        if is_good(s):
            out.append({"change": change, "evidence": s})

    if cur["rsi_max"] < 35:
        add(f"raise rsi_max from {cur['rsi_max']} to 35",
            [r for r in data if passes(r, cur, "rsi") and cur["rsi_max"] <= r["rsi"] < 35])
    if cur["stabilization"] != "none":
        add("drop the stabilization requirement",
            [r for r in data if passes(r, cur, "stab") and not r["stab_rsi_up"]])
    if cur["require_uptrend_200"]:
        add("drop the above-200-day requirement",
            [r for r in data if passes(r, cur, "trend") and not r["above_ma200"]])
    if cur["market_filter"]:
        add("drop the market filter", [r for r in data if passes(r, cur, "market") and not r["spy_ok"]])
    if cur["min_depth_pct"] > base["min_depth_pct"]:
        add(f"lower min depth from {cur['min_depth_pct']}% to {base['min_depth_pct']}%",
            [r for r in data if passes(r, cur, "depth") and base["min_depth_pct"] <= r["depth"] < cur["min_depth_pct"]])
    for sym in cur.get("blocked_symbols", []):
        add(f"unblock {sym}", [r for r in data if r["symbol"] == sym])
    # Name-level: non-core names whose shadow rows keep working.
    for sym in sorted({r["symbol"] for r in data if r["tier"] != "core" and r["would_trade"]}):
        add(f"consider {sym} for the core (shadow-only so far)",
            [r for r in data if r["symbol"] == sym and r["would_trade"]])
    return out


def write_proposals(new: list, today: str, path: str = None) -> list:
    path = path or PROPOSALS
    os.makedirs(os.path.dirname(path), exist_ok=True)
    existing = []
    if os.path.exists(path):
        try:
            with open(path) as f:
                existing = json.load(f)
        except (OSError, ValueError):
            existing = []
    have = {p["change"] for p in existing if p.get("status") == "PENDING"}
    fresh = []
    for p in new:
        if p["change"] in have:
            continue
        rec = {"id": f"rb-{today}-{len(existing) + len(fresh) + 1}", "date": today, "status": "PENDING", **p}
        fresh.append(rec)
    if fresh:
        with open(path, "w") as f:
            json.dump(existing + fresh, f, indent=2)
    return fresh


# ── Run ───────────────────────────────────────────────────────────────────────
def learn(today: str = None, ledger_path: str = None, learned_path: str = None,
          proposals_path: str = None) -> dict:
    today = today or et_today_iso()
    rows = rebound.load_ledger(ledger_path)
    base = {**rebound.live_params(learned_path="/nonexistent")}      # defaults, no learning
    learned, evidence = derive(rows, base)
    applied = rebound.tighten_only(base, learned)
    cur = applied
    fresh = write_proposals(propose(rows, cur, base), today, proposals_path)

    out = {
        "scope": rebound.LEARNED_SCOPE, "generated": today, "agent_version": get_version(),
        "sample": {"ledger_rows": len(rows), "resolved": len(resolved(rows))},
        "applied": learned, "evidence": evidence,
    }
    path = learned_path or rebound.LEARNED_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(out, f, indent=2)
    out["new_proposals"] = fresh
    return out


def alert_proposals(fresh: list) -> bool:
    import notify
    if not fresh:
        return False
    body = "\n".join(f"- {p['change']}  (n={p['evidence']['n']}, win {p['evidence']['win']:.0%}, "
                     f"median {p['evidence']['median']:+.1%})" for p in fresh)
    return notify.alert("Rebound path: the agent is proposing a change", body + "\n\nNothing is applied "
                        "until you approve; see logs/proposals/rebound-proposals.json", tags="bulb")


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "learn"
    if action == "learn":
        res = learn()
        print(json.dumps({k: res[k] for k in ("generated", "sample", "applied", "evidence")}, indent=2))
        alert_proposals(res["new_proposals"])
    else:
        print(__doc__)
        sys.exit(2)
