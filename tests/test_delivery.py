"""Delivery-state contracts for the Codex automation; all transports are mocked."""
from __future__ import annotations

import datetime as dt
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPTS = Path(__file__).resolve().parents[1] / "stock-signal/scripts"
sys.path.insert(0, str(SCRIPTS))
import codex_cycle as cycle
import push


SLACK_RECEIPT = "https://example.slack.com/archives/D123/p1234567890000000"


class DeliveryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 9, 22, 14, 0)
        self.plan = {"总资金": 100000, "watchlist": [{"code": "sz000560", "name": "样例", "enabled": True}]}
        (self.root / "plan.json").write_text(json.dumps(self.plan), encoding="utf-8")
        self.snapshot = {
            "as_of": self.now.isoformat(),
            "quotes": {"sz000560": {"price": 3.3, "time": "20260922140000"}},
            "emotion": {"zt": 20, "zb": 2, "dt": 1, "zb_rate": 10.0, "max_lb": 3},
            "warnings": [],
        }
        self.decision = {"buy_allowed": False, "risk_reasons": [], "stocks": [
            {"code": "sz000560", "name": "样例", "action": "观察", "reason": "等待", "price": 3.3}
        ]}
        self.jev = {"model": "test", "answers": {
            "market": {"choice": "分歧"}, "mainline": {"choice": "无明确主线"},
            "action_sz000560": {"choice": "观察"}, "reason_sz000560": {"choice": "等待确认"},
        }}

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def run_at(self, now: dt.datetime | None = None, analyzer=None):
        return cycle.run_cycle(
            self.root, now or self.now,
            collector=lambda _plan, _now: self.snapshot,
            evaluator=lambda _snapshot, _plan: self.decision,
            analyzer=analyzer or (lambda _snapshot, _plan, _decision: self.jev),
        )

    def test_claim_twice_requires_verification(self) -> None:
        report = self.run_at()
        self.assertEqual(cycle.claim(self.root, report["report_id"], "slack", self.now)["status"], "ready_to_send")
        self.assertEqual(cycle.claim(self.root, report["report_id"], "slack", self.now)["status"], "needs_verification")

    def test_claim_blocks_newer_report_but_pending_keeps_unconfirmed_visible(self) -> None:
        first = self.run_at()
        cycle.claim(self.root, first["report_id"], "slack", self.now)
        self.run_at(self.now + dt.timedelta(minutes=5))
        pending = cycle.pending(self.root, self.now + dt.timedelta(minutes=5))
        self.assertEqual(pending[0]["status"], "needs_verification")
        self.assertEqual(pending[0]["report_id"], first["report_id"])

    def test_verified_absent_release_allows_one_more_claim(self) -> None:
        report = self.run_at()
        cycle.claim(self.root, report["report_id"], "slack", self.now)
        released = cycle.release(self.root, report["report_id"], "slack", "verified_absent:searched target by report id", self.now)
        self.assertEqual(released["status"], "released")
        self.assertEqual(cycle.claim(self.root, report["report_id"], "slack", self.now)["status"], "ready_to_send")

    def test_late_ack_records_receipt_without_changing_deduplication(self) -> None:
        report = self.run_at()
        cycle.claim(self.root, report["report_id"], "slack", self.now)
        cycle.ack(self.root, report["report_id"], "slack", SLACK_RECEIPT, self.now + dt.timedelta(hours=2))
        state = cycle.load(cycle.folder(self.root) / "state.json")
        self.assertNotIn("slack", state.get("delivered", {}))
        self.assertEqual(cycle.read_record(self.root, report["report_id"])["channels"]["slack"]["status"], "delivered")

    def test_out_of_order_ack_does_not_move_delivered_at_backwards(self) -> None:
        first = self.run_at()
        second = self.run_at(self.now + dt.timedelta(minutes=5))
        cycle.ack(self.root, second["report_id"], "slack", SLACK_RECEIPT, self.now + dt.timedelta(minutes=5))
        cycle.ack(self.root, first["report_id"], "slack", SLACK_RECEIPT, self.now + dt.timedelta(minutes=6))
        state = cycle.load(cycle.folder(self.root) / "state.json")
        self.assertEqual(state["delivered_at"]["slack"], cycle.read_record(self.root, second["report_id"])["created_at"])

    def test_feishu_delivery_states_are_per_channel_and_never_retried_when_uncertain(self) -> None:
        report = self.run_at()
        with patch.object(push, "_load_env"), patch.object(push, "send_feishu_once", return_value=(True, "feishu-ok")) as sender, patch.object(push, "ENV", {"LARK_USER_OPEN_ID": "open"}), patch.dict(os.environ, {"NO_PUSH": ""}):
            result = cycle.send_feishu(self.root, report["report_id"], self.now)
        self.assertEqual(result["pending_channels"], ["slack"])
        sender.assert_called_once()

        self.jev = {**self.jev, "answers": {**self.jev["answers"], "market": {"choice": "回暖"}}}
        uncertain = self.run_at(self.now + dt.timedelta(minutes=5))
        with patch.object(push, "_load_env"), patch.object(push, "send_feishu_once", return_value=(False, "timeout")) as sender, patch.object(push, "ENV", {"LARK_USER_OPEN_ID": "open"}), patch.dict(os.environ, {"NO_PUSH": ""}):
            self.assertEqual(cycle.send_feishu(self.root, uncertain["report_id"], self.now + dt.timedelta(minutes=5))["status"], "delivery_uncertain")
            self.assertEqual(cycle.send_feishu(self.root, uncertain["report_id"], self.now + dt.timedelta(minutes=6))["status"], "needs_verification")
        sender.assert_called_once()

        self.jev = {**self.jev, "answers": {**self.jev["answers"], "market": {"choice": "高潮"}}}
        dry = self.run_at(self.now + dt.timedelta(minutes=10))
        with patch.object(push, "_load_env") as loader, patch.object(push, "send_feishu_once") as sender, patch.dict(os.environ, {"NO_PUSH": "1"}):
            self.assertEqual(cycle.send_feishu(self.root, dry["report_id"], self.now + dt.timedelta(minutes=10))["status"], "dry_run")
        loader.assert_not_called()
        sender.assert_not_called()

    def test_heartbeat_and_each_auction_slot_run_jev(self) -> None:
        calls: list[dt.datetime] = []
        def analyze(_snapshot, _plan, _decision):
            calls.append(dt.datetime.now())
            return self.jev
        for now in (self.now.replace(hour=9, minute=15), self.now.replace(hour=9, minute=20), self.now.replace(hour=9, minute=25)):
            self.assertEqual(self.run_at(now, analyze)["status"], "success")
        self.assertEqual(len(calls), 3)

    def test_invalid_plan_writes_error_report(self) -> None:
        (self.root / "plan.json").write_text("{}", encoding="utf-8")
        result = cycle.run_cycle(
            self.root, self.now,
            collector=lambda *_args: self.fail("invalid plan must not collect"),
            evaluator=lambda *_args: {"buy_allowed": False, "risk_reasons": [], "stocks": []},
            analyzer=lambda *_args: self.fail("invalid plan must not call Jev"),
        )
        self.assertEqual(result["status"], "error")
        self.assertEqual(cycle.read_record(self.root, result["report_id"])["error_type"], "ValueError")

    def test_close_archive_keeps_legacy_archive_and_has_private_permissions(self) -> None:
        reviews = self.root / "reviews"
        reviews.mkdir()
        legacy = reviews / "2026-09-22.md"
        legacy.write_text("legacy close\n", encoding="utf-8")
        self.run_at(self.now.replace(hour=15, minute=5))
        archive = reviews / "2026-09-22-codex.md"
        self.assertEqual(legacy.read_text(encoding="utf-8"), "legacy close\n")
        self.assertTrue(archive.is_file())
        self.assertEqual(stat.S_IMODE(reviews.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(archive.stat().st_mode), 0o600)


if __name__ == "__main__":
    unittest.main()
