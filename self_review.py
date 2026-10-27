#!/usr/bin/env python3
"""
self_review.py — recursive self-improvement proposal log.

Scans the same calibration/round-trip evidence learning_agent.py already
collects and turns any pattern it has no safe automatic handler for into a
dated, structured PROPOSAL in logs/proposals/proposals.json for Alex to
approve or reject.

What this script does NOT do, on purpose: it never edits CLAUDE.md, never
edits code, never touches EQUITIES_VERSION, and never calls version.py. It
only proposes. Approving a proposal here authorizes the *idea* -- actually
implementing it (editing the code/CLAUDE.md, writing tests, bumping the
version, committing) stays a separate, reviewed step, the same way every
other version change in this repo has happened. "No auto-graduation ever"
applies to the agent promoting its own code, not just to trading real money.

This is deliberately additive to, not a replacement for, learning_agent.py's
existing mechanism. Three of detect_patterns()'s actions already have a safe,
narrow, automatic handler in learning_agent.update_weight_hints() --
reduce_news_weight, tighten_risk_off_gate, raise_approve_threshold -- gated
by judge.py's four rails (scope, graduated sample, tighten-only, clamp+log).
Those stay exactly as they are; this script does not re-propose them. Only
patterns with NO automatic handler (today: tighten_entry_checklist,
tighten_tier3_stop, and any new action string detect_patterns grows later)
become proposals here, because those currently have no path forward except
turning into inert prose in CLAUDE.md's Lessons Learned section.

Usage:
  python3 self_review.py propose              # scan evidence, write/refresh proposals
  python3 self_review.py list                 # show PENDING proposals
  python3 self_review.py show <id>            # full detail for one proposal
  python3 self_review.py approve <id> [note]  # mark APPROVED (does not implement it)
  python3 self_review.py reject <id> [note]   # mark REJECTED
"""

import json
import sys
from pathlib import Path

import learning_agent
from market_time import et_today_iso

ROOT          = Path(__file__).parent
PROPOSAL_DIR  = ROOT / "logs" / "proposals"
PROPOSAL_FILE = PROPOSAL_DIR / "proposals.json"

# Actions learning_agent.update_weight_hints() already applies safely and
# automatically, under judge.py's tighten-only/graduated-sample/clamp rails.
# Re-proposing these here would be redundant with a mechanism that's already
# tested and already gated.
AUTO_HANDLED_ACTIONS = {
    "reduce_news_weight",
    "tighten_risk_off_gate",
    "raise_approve_threshold",
}

# Patterns that are informational summaries, not an actionable change.
INFORMATIONAL_ACTIONS = {
    "graduation_check",
}

# What each currently-unhandled action means as a concrete proposed change,
# and the versioning-table level it would be if approved and implemented.
# Tightening a documented floor is MINOR (CLAUDE.md's versioning table);
# these are all tightening-direction by construction of detect_patterns().
ACTION_DETAIL = {
    "tighten_entry_checklist": {
        "proposed_change": (
            "Add an explicit entry-timing confirmation to the Entry Checklist: "
            "e.g. require RSI(14) strictly below 45 (not just 'wait' in the "
            "45-60 band), or require two consecutive closes at/below MA20 "
            "before entry. Evidence is trades that made money despite a "
            "Reviewer score <5.0 -- the outcome was fine but the entry "
            "discipline that's supposed to predict it was not."
        ),
        "level": "MINOR",
    },
    "tighten_tier3_stop": {
        "proposed_change": (
            "Tighten Hard Rule 3's Tier 3 (speculative) stop-loss from 10% to "
            "7%, and/or add an average-daily-volume floor (e.g. exclude "
            "symbols with avg volume <1M shares) to the Tier 3 definition."
        ),
        "level": "MINOR",
    },
}
DEFAULT_DETAIL = {
    "proposed_change": "No mapped remediation yet for this pattern -- review the evidence and decide manually.",
    "level": "MINOR",
}


def _load() -> dict:
    if PROPOSAL_FILE.exists():
        return json.loads(PROPOSAL_FILE.read_text())
    return {"proposals": []}


def _save(data: dict) -> None:
    PROPOSAL_DIR.mkdir(parents=True, exist_ok=True)
    PROPOSAL_FILE.write_text(json.dumps(data, indent=2) + "\n")


def propose() -> dict:
    """Scan current evidence and write/refresh proposals. Never implements anything."""
    trades = learning_agent.load_calibration()
    judge_lines = learning_agent.load_judge_lines()
    patterns = learning_agent.detect_patterns(trades, judge_lines)

    data = _load()
    by_pattern = {p["pattern_id"]: p for p in data["proposals"]}
    today = et_today_iso()
    created, refreshed = [], []

    for pat in patterns:
        action = pat["action"]
        if action in AUTO_HANDLED_ACTIONS or action in INFORMATIONAL_ACTIONS:
            continue  # already safely automatic, or purely informational

        detail = ACTION_DETAIL.get(action, DEFAULT_DETAIL)
        existing = by_pattern.get(pat["id"])

        if existing is None:
            entry = {
                "id": f"{today}-{pat['id']}",
                "pattern_id": pat["id"],
                "first_seen": today,
                "last_updated": today,
                "count": pat["count"],
                "evidence": pat["desc"],
                "proposed_change": detail["proposed_change"],
                "suggested_version_level": detail["level"],
                "status": "PENDING",
                "decided_date": None,
                "decision_note": None,
                "count_at_decision": None,
            }
            data["proposals"].append(entry)
            by_pattern[pat["id"]] = entry
            created.append(entry["id"])
            continue

        if existing["status"] == "PENDING":
            existing["last_updated"] = today
            existing["count"] = pat["count"]
            existing["evidence"] = pat["desc"]
            refreshed.append(existing["id"])
            continue

        # Previously decided (APPROVED/REJECTED). Only raise it again if the
        # evidence has actually grown since that decision -- a rejected
        # pattern recurring at the same count is not new information.
        if existing["count_at_decision"] is not None and pat["count"] > existing["count_at_decision"]:
            entry = {
                "id": f"{today}-{pat['id']}",
                "pattern_id": pat["id"],
                "first_seen": today,
                "last_updated": today,
                "count": pat["count"],
                "evidence": pat["desc"],
                "proposed_change": detail["proposed_change"],
                "suggested_version_level": detail["level"],
                "status": "PENDING",
                "decided_date": None,
                "decision_note": (
                    f"Recurrence of {existing['id']} ({existing['status']} on "
                    f"{existing['decided_date']}: {existing['decision_note']!r}), "
                    f"now at count={pat['count']} vs {existing['count_at_decision']} then."
                ),
                "count_at_decision": None,
            }
            data["proposals"].append(entry)
            by_pattern[pat["id"]] = entry
            created.append(entry["id"])

    _save(data)
    return {"created": created, "refreshed": refreshed}


def list_pending() -> list:
    data = _load()
    return [p for p in data["proposals"] if p["status"] == "PENDING"]


def _find(proposal_id: str) -> dict | None:
    data = _load()
    for p in data["proposals"]:
        if p["id"] == proposal_id:
            return p
    return None


def decide(proposal_id: str, status: str, note: str | None) -> dict:
    assert status in ("APPROVED", "REJECTED")
    data = _load()
    for p in data["proposals"]:
        if p["id"] == proposal_id:
            p["status"] = status
            p["decided_date"] = et_today_iso()
            p["decision_note"] = note
            p["count_at_decision"] = p["count"]
            _save(data)
            return p
    raise KeyError(f"No proposal with id {proposal_id!r}")


def _format(p: dict) -> str:
    lines = [
        f"[{p['id']}] {p['status']}",
        f"  Pattern: {p['pattern_id']}  (seen {p['count']}x, first {p['first_seen']}, last updated {p['last_updated']})",
        f"  Evidence: {p['evidence']}",
        f"  Proposed change ({p['suggested_version_level']}): {p['proposed_change']}",
    ]
    if p["status"] != "PENDING":
        lines.append(f"  Decided {p['decided_date']}: {p['decision_note']}")
    return "\n".join(lines)


if __name__ == "__main__":
    action = sys.argv[1] if len(sys.argv) > 1 else "list"

    if action == "propose":
        result = propose()
        print(json.dumps(result, indent=2))
    elif action == "list":
        pending = list_pending()
        if not pending:
            print("No pending proposals.")
        for p in pending:
            print(_format(p))
            print()
    elif action == "show" and len(sys.argv) > 2:
        p = _find(sys.argv[2])
        print(_format(p) if p else f"No proposal with id {sys.argv[2]!r}")
    elif action in ("approve", "reject") and len(sys.argv) > 2:
        note = sys.argv[3] if len(sys.argv) > 3 else None
        p = decide(sys.argv[2], "APPROVED" if action == "approve" else "REJECTED", note)
        print(_format(p))
    else:
        print(__doc__)
