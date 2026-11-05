#!/usr/bin/env python3
"""
universe_policy.py — core-universe and track-record gating for the equities Judge.

Two tighten-only rules, applied to a finished Judge score (never before it):

  1. CORE UNIVERSE. Only names in core_universe.json "core" may be APPROVEd.
     Everything else (watchlist probation, discovery) is research-only: it is
     still researched and scored, but an APPROVE is capped at HOLD. "hedge"
     names (Bearish Playbook instruments) are exempt from both rules.
  2. RECORD GATE. A name's approval bar rises when it has no closed-trade
     record, a thin one, or a losing one -- see record_gate().

Rules only ever turn APPROVE into HOLD. They never upgrade a recommendation and
never lower a gate, so by CLAUDE.md's versioning table this is a tightening.

Shadow mode: while core_universe.json "status" is "proposed", the policy is
computed and recorded but changes nothing, so the effect can be reviewed before
it is switched on. Only "approved" enforces.
"""

import json
import os
from datetime import date

ROOT        = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(ROOT, "core_universe.json")

NO_RECORD_GATE     = 8.0   # zero closed trades
THIN_RECORD_GATE   = 7.5   # 1-2 closed trades: not enough to judge either way
LOSING_RECORD_GATE = 8.0   # 3+ closed trades and a negative average P&L
MIN_RECORD_TRADES  = 3     # trades needed before a record counts as established

VALID_STATUS = {"proposed", "approved"}


def load_config(path: str = None):
    """Return the parsed config, or None if no config file exists (feature not
    set up). A file that exists but is malformed raises: with real-money orders
    downstream, a silently ignored policy is worse than a loud failure."""
    path = path or CONFIG_PATH
    if not os.path.exists(path):
        return None
    with open(path) as f:
        cfg = json.load(f)

    if cfg.get("status") not in VALID_STATUS:
        raise ValueError(f"core_universe.json: status must be one of {sorted(VALID_STATUS)}, "
                         f"got {cfg.get('status')!r}")
    buckets = {}
    for name in ("core", "hedge", "probation"):
        raw = cfg.get(name, {})
        if not isinstance(raw, (dict, list)):
            raise ValueError(f"core_universe.json: {name!r} must be an object or list")
        syms = list(raw)
        if any(not isinstance(s, str) or s != s.upper() or not s for s in syms):
            raise ValueError(f"core_universe.json: {name!r} holds a non-uppercase symbol")
        buckets[name] = set(syms)
    if not buckets["core"]:
        raise ValueError("core_universe.json: 'core' is empty")
    names = list(buckets["core"]) + list(buckets["hedge"]) + list(buckets["probation"])
    dupes = sorted({s for s in names if names.count(s) > 1})
    if dupes:
        raise ValueError(f"core_universe.json: symbols in more than one bucket: {dupes}")

    # The weekly review needs each core name's `added` date (grace period) and
    # the speculative flag drives the Judge's speculative rules.
    entries = cfg["core"]
    if not isinstance(entries, dict):
        raise ValueError("core_universe.json: 'core' must be an object keyed by symbol")
    speculative = set()
    for sym, entry in entries.items():
        if not isinstance(entry, dict):
            raise ValueError(f"core_universe.json: core entry {sym} must be an object")
        try:
            date.fromisoformat(entry.get("added", ""))
        except (TypeError, ValueError):
            raise ValueError(f"core_universe.json: core entry {sym} needs an ISO 'added' date")
        if not isinstance(entry.get("speculative", False), bool):
            raise ValueError(f"core_universe.json: core entry {sym} 'speculative' must be true/false")
        if entry.get("speculative"):
            speculative.add(sym)

    return {"status": cfg["status"],
            "enforced": cfg["status"] == "approved",
            "entries": entries,
            "speculative": speculative,
            **buckets}


def speculative_symbols(path: str = None) -> set:
    """Core names Alex tagged speculative. Empty in shadow mode or with no
    config, so a proposed list changes nothing until it is approved."""
    cfg = load_config(path)
    if cfg is None or not cfg["enforced"]:
        return set()
    return cfg["speculative"]


def bucket_of(symbol: str, cfg: dict) -> str:
    if symbol in cfg["core"]:
        return "core"
    if symbol in cfg["hedge"]:
        return "hedge"
    return "outside_core"


def record_gate(record):
    """(gate, label) for a symbol's stats record. The gate is a floor to be
    combined with the Judge's own gate via max(); it never lowers anything."""
    n = (record or {}).get("trades", 0)
    if n == 0:
        return NO_RECORD_GATE, "no closed trades"
    if n < MIN_RECORD_TRADES:
        return THIN_RECORD_GATE, f"thin record ({n} closed)"
    if (record.get("avg_pnl_pct") or 0) < 0:
        return LOSING_RECORD_GATE, f"losing record ({n} closed, {record['avg_pnl_pct']:+.2f}% avg)"
    return 0.0, f"established record ({n} closed, {record['avg_pnl_pct']:+.2f}% avg)"


def apply(scored: dict, symbol: str, record, cfg) -> dict:
    """Attach `universe_policy` to a scored result and, when enforced, cap a
    blocked APPROVE at HOLD. Returns the same dict."""
    if cfg is None:
        return scored

    bucket = bucket_of(symbol, cfg)
    policy = {
        "status":   cfg["status"],
        "enforced": cfg["enforced"],
        "bucket":   bucket,
        "record":   record,
        "would_change": None,
    }

    if bucket != "hedge":
        gate, label = record_gate(record)
        base = scored.get("approval_gate") or 0.0
        policy["record_gate"]  = gate
        policy["record_label"] = label
        policy["required_gate"] = max(base, gate)
    else:
        policy["required_gate"] = scored.get("approval_gate")

    if scored.get("recommendation") == "APPROVE":
        reason = None
        if bucket == "outside_core":
            reason = "research-only: outside the core universe"
        elif bucket == "core" and scored.get("score", 0) < policy["required_gate"]:
            reason = (f"score {scored['score']} below {policy['required_gate']} "
                      f"required for a {policy['record_label']}")
        if reason:
            policy["would_change"] = f"APPROVE -> HOLD ({reason})"
            if cfg["enforced"]:
                scored["recommendation"] = "HOLD"
                scored["approval_gate"] = policy["required_gate"]
                scored["rationale"] = f"{scored.get('rationale', '')} UNIVERSE: {reason}."

    scored["universe_policy"] = policy
    return scored
