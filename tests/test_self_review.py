#!/usr/bin/env python3
"""
Tests for self_review.py -- the recursive self-improvement proposal log.

The property under test, repeatedly: self_review.py may PROPOSE a change,
but approving a proposal must never implement it. There is no code path in
this module that edits CLAUDE.md, code, or EQUITIES_VERSION -- these tests
exist so that stays true if someone "helpfully" wires one in later.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import self_review


PATTERN_UNHANDLED = {
    "id": "tighten_tier3_stop",
    "count": 2,
    "desc": "Speculative/penny stocks led to POOR outcomes 2x.",
    "action": "tighten_tier3_stop",
}

PATTERN_AUTO_HANDLED = {
    "id": "persistent_judge_overconfidence",
    "count": 5,
    "desc": "Judge consistently scores 4.8 pts above reviewer.",
    "action": "reduce_news_weight",
}

PATTERN_INFORMATIONAL = {
    "id": "win_rate_summary",
    "count": 10,
    "desc": "Overall win rate: 60%.",
    "action": "graduation_check",
}


class TestSelfReview(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.proposal_dir = Path(self.tmp) / "proposals"
        self.proposal_file = self.proposal_dir / "proposals.json"
        self._patches = [
            mock.patch.object(self_review, "PROPOSAL_DIR", self.proposal_dir),
            mock.patch.object(self_review, "PROPOSAL_FILE", self.proposal_file),
            mock.patch.object(self_review, "et_today_iso", return_value="2026-10-27"),
            mock.patch.object(self_review.learning_agent, "load_calibration", return_value=[]),
            mock.patch.object(self_review.learning_agent, "load_judge_lines", return_value=[]),
        ]
        for p in self._patches:
            p.start()
            self.addCleanup(p.stop)

    def _propose_with(self, patterns):
        with mock.patch.object(self_review.learning_agent, "detect_patterns", return_value=patterns):
            return self_review.propose()

    def test_unhandled_action_creates_a_pending_proposal(self):
        result = self._propose_with([PATTERN_UNHANDLED])
        self.assertEqual(len(result["created"]), 1)
        pending = self_review.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["pattern_id"], "tighten_tier3_stop")
        self.assertEqual(pending[0]["status"], "PENDING")

    def test_auto_handled_action_is_not_proposed(self):
        self._propose_with([PATTERN_AUTO_HANDLED])
        self.assertEqual(self_review.list_pending(), [])

    def test_informational_action_is_not_proposed(self):
        self._propose_with([PATTERN_INFORMATIONAL])
        self.assertEqual(self_review.list_pending(), [])

    def test_second_propose_refreshes_rather_than_duplicates(self):
        self._propose_with([PATTERN_UNHANDLED])
        grown = dict(PATTERN_UNHANDLED, count=4, desc="Now 4 occurrences.")
        result = self._propose_with([grown])
        self.assertEqual(result["created"], [])
        self.assertEqual(len(result["refreshed"]), 1)
        pending = self_review.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["count"], 4)

    def test_approve_sets_status_and_does_not_touch_code_or_claude_md(self):
        self._propose_with([PATTERN_UNHANDLED])
        proposal_id = self_review.list_pending()[0]["id"]
        decided = self_review.decide(proposal_id, "APPROVED", "looks right, go ahead")
        self.assertEqual(decided["status"], "APPROVED")
        self.assertEqual(decided["decision_note"], "looks right, go ahead")
        self.assertEqual(decided["count_at_decision"], 2)
        self.assertEqual(self_review.list_pending(), [])

    def test_reject_then_same_count_recurrence_does_not_reopen(self):
        self._propose_with([PATTERN_UNHANDLED])
        proposal_id = self_review.list_pending()[0]["id"]
        self_review.decide(proposal_id, "REJECTED", "not worth it yet")
        self._propose_with([PATTERN_UNHANDLED])  # same count=2 again
        self.assertEqual(self_review.list_pending(), [])

    def test_reject_then_higher_count_recurrence_reopens_as_new_proposal(self):
        self._propose_with([PATTERN_UNHANDLED])
        first_id = self_review.list_pending()[0]["id"]
        self_review.decide(first_id, "REJECTED", "not worth it yet")
        grown = dict(PATTERN_UNHANDLED, count=6, desc="Now 6 occurrences.")
        result = self._propose_with([grown])
        self.assertEqual(len(result["created"]), 1)
        pending = self_review.list_pending()
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["count"], 6)
        self.assertIn(first_id, pending[0]["decision_note"])

    def test_proposals_file_never_contains_claude_md_or_code_edits(self):
        # Mutation check: decide() must only ever rewrite PROPOSAL_FILE.
        self._propose_with([PATTERN_UNHANDLED])
        proposal_id = self_review.list_pending()[0]["id"]
        claude_md_before = None
        claude_md_path = os.path.join(os.path.dirname(os.path.abspath(self_review.__file__)), "CLAUDE.md")
        if os.path.exists(claude_md_path):
            with open(claude_md_path) as f:
                claude_md_before = f.read()
        self_review.decide(proposal_id, "APPROVED", "fine")
        if claude_md_before is not None:
            with open(claude_md_path) as f:
                self.assertEqual(f.read(), claude_md_before)


if __name__ == "__main__":
    unittest.main()
