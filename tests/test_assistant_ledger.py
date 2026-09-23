import datetime as dt
import json
import os
import stat
import tempfile
import threading
import unittest
from pathlib import Path

from assistant.ledger import Ledger


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.ledger = Ledger(Path(self.tmp.name) / "ledger.sqlite3")
        self.when = dt.datetime(2026, 9, 21, 10, 0)

    def tearDown(self):
        self.tmp.cleanup()

    def recommendation(self, **overrides):
        record = {
            "id": "rec-1", "report_id": "report-1", "code": "sz000001",
            "name": "平安银行", "recommended_at": self.when.isoformat(), "price": "10",
            "action": "buy", "reason": "test", "entry_condition": "price <= 10",
            "invalidation": "price < 9", "strategy_version": "v1",
            "expires_at": (self.when + dt.timedelta(days=1)).isoformat(), "budget": "20000",
        }
        record.update(overrides)
        return record

    def test_ingest_is_idempotent_but_rejects_a_changed_id(self):
        record = self.recommendation()
        self.assertEqual(self.ledger.ingest([record]), {"inserted": 1, "existing": 0})
        self.assertEqual(self.ledger.ingest([record]), {"inserted": 0, "existing": 1})
        with self.assertRaises(ValueError):
            self.ledger.ingest([self.recommendation(price="11")])

    def test_expired_recommendation_cannot_create_order(self):
        self.ledger.ingest([self.recommendation(expires_at="2026-09-21T10:30:00")])
        result = self.ledger.create_order("rec-1", self.when + dt.timedelta(hours=1))
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "expired")

    def test_same_or_earlier_quote_never_fills_pending_order(self):
        self.ledger.ingest([self.recommendation()])
        order = self.ledger.create_order("rec-1", self.when)
        result = self.ledger.process_quotes([{
            "code": "sz000001", "time": self.when.isoformat(), "price": "9.9", "volume": 1,
            "limit_up": "11", "limit_down": "9",
        }], self.when)
        self.assertEqual(result["filled"], 0)
        self.assertEqual(self.ledger.summary(self.when)["orders"][0]["status"], "pending")
        self.assertEqual(order["status"], "pending")

    def test_future_quote_never_fills_against_explicit_processing_clock(self):
        self.ledger.ingest([self.recommendation()])
        self.ledger.create_order("rec-1", self.when)
        self.ledger.process_quotes([{
            "code": "sz000001", "time": "2026-09-21T10:02:00", "price": "9.9", "volume": 1,
            "limit_up": "11", "limit_down": "9",
        }], self.when + dt.timedelta(minutes=1))
        self.assertEqual(self.ledger.summary(self.when + dt.timedelta(minutes=1))["orders"][0]["reason"], "future_quote")

    def test_nonpositive_terms_are_archived_but_cannot_create_an_order(self):
        self.ledger.ingest([self.recommendation(price="0", budget="0")])
        result = self.ledger.create_order("rec-1", self.when)
        self.assertEqual(result["status"], "rejected")
        self.assertEqual(result["reason"], "invalid_order_terms")

    def test_missing_price_and_budget_are_archived_but_cannot_create_an_order(self):
        self.ledger.ingest([self.recommendation(price=None, budget=None)])
        stored = self.ledger.summary(self.when)["recommendations"][0]
        self.assertIsNone(stored["price"])
        self.assertIsNone(stored["budget"])
        result = self.ledger.create_order("rec-1", self.when)
        self.assertEqual((result["status"], result["reason"]), ("rejected", "invalid_order_terms"))

    def test_unknown_strategy_version_is_archived_without_invention(self):
        self.ledger.ingest([self.recommendation(strategy_version=None)])
        self.assertIsNone(self.ledger.summary(self.when)["recommendations"][0]["strategy_version"])

    def test_empty_simulation_reports_null_profit_metrics_and_is_json_safe(self):
        summary = self.ledger.summary(self.when)
        self.assertIsNone(summary["metrics"]["realized_pnl"])
        self.assertIsNone(summary["metrics"]["unrealized_pnl"])
        self.assertEqual(summary["metrics"]["capital_denominator"], "100000")
        self.assertFalse(summary["metrics"]["historical_backtest"])
        json.dumps(summary, ensure_ascii=False)

    def test_limit_up_quote_never_fills(self):
        self.ledger.ingest([self.recommendation()])
        self.ledger.create_order("rec-1", self.when)
        self.ledger.process_quotes([{
            "code": "sz000001", "time": "2026-09-21T10:01:00", "price": "11", "volume": 9,
            "limit_up": "11", "limit_down": "9",
        }], dt.datetime(2026, 9, 21, 10, 2))
        order = self.ledger.summary(dt.datetime(2026, 9, 21, 10, 2))["orders"][0]
        self.assertEqual(order["status"], "pending")
        self.assertEqual(order["reason"], "at_limit_up")

    def test_pending_reservations_cap_cash_and_per_stock_exposure(self):
        self.ledger.ingest([self.recommendation(budget="100000")])
        order = self.ledger.create_order("rec-1", self.when)
        self.assertEqual(order["quantity"], 1900)
        self.assertLessEqual(float(order["reserved_cash"]), 20000)
        self.assertEqual(self.ledger.create_order("rec-1", self.when)["id"], order["id"])

    def test_close_requires_next_weekday_and_tradable_quote(self):
        self.ledger.ingest([self.recommendation(recommended_at="2026-09-18T10:00:00")])
        self.ledger.create_order("rec-1", dt.datetime(2026, 9, 18, 10, 0))
        self.ledger.process_quotes([{
            "code": "sz000001", "time": "2026-09-18T10:01:00", "price": "9.9", "volume": 1,
            "limit_up": "11", "limit_down": "9",
        }], dt.datetime(2026, 9, 18, 10, 2))
        self.assertEqual(self.ledger.close_position("sz000001", {
            "code": "sz000001", "time": "2026-09-18T14:00:00", "price": "9.8", "volume": 1, "limit_down": "8.9", "limit_up": "11",
        }, dt.datetime(2026, 9, 18, 14, 1))["status"], "rejected")
        self.assertEqual(self.ledger.close_position("sz000001", {
            "code": "sz000001", "time": "2026-09-21T10:00:00", "price": "9.8", "volume": 1, "limit_down": "8.9", "limit_up": "11",
        }, dt.datetime(2026, 9, 21, 10, 1))["status"], "filled")

    def test_live_analysis_formal_buy_candidate_is_accepted_but_queue_is_not(self):
        self.ledger.ingest([self.recommendation(action="买入候选")])
        self.assertEqual(self.ledger.create_order("rec-1", self.when)["status"], "pending")
        self.ledger.ingest([self.recommendation(id="rec-queue", action="排板候选", entry_condition="paper_normal_buy")])
        self.assertEqual(self.ledger.create_order("rec-queue", self.when)["reason"], "queue_snapshot_not_allowed")

    def test_board_entry_type_blocks_generic_formal_action(self):
        for entry_type in ("排板候选", "回封候选"):
            with self.subTest(entry_type=entry_type):
                ledger = Ledger(Path(self.tmp.name) / f"{entry_type}.sqlite3")
                ledger.ingest([self.recommendation(action="买入候选", entry_type=entry_type)])
                self.assertEqual(ledger.create_order("rec-1", self.when)["reason"], "queue_snapshot_not_allowed")
                self.assertEqual(ledger.summary(self.when)["recommendations"][0]["entry_type"], entry_type)
                ledger.close()

    def test_create_order_requires_a_weekday_trading_session(self):
        self.ledger.ingest([self.recommendation()])
        result = self.ledger.create_order("rec-1", dt.datetime(2026, 9, 21, 12, 0))
        self.assertEqual(result["reason"], "outside_trading_session")

    def test_order_cannot_precede_recommendation(self):
        self.ledger.ingest([self.recommendation(recommended_at="2026-09-21T10:01:00")])
        self.assertEqual(self.ledger.create_order("rec-1", self.when)["reason"], "not_yet_recommended")

    def test_requires_volume_session_and_fresh_quote(self):
        self.ledger.ingest([self.recommendation()])
        self.ledger.create_order("rec-1", self.when)
        base = {"code": "sz000001", "time": "2026-09-21T10:01:00", "price": "9.9", "limit_up": "11", "limit_down": "9"}
        self.ledger.process_quotes([base], dt.datetime(2026, 9, 21, 10, 2))
        self.assertEqual(self.ledger.summary(dt.datetime(2026, 9, 21, 10, 2))["orders"][0]["reason"], "no_liquidity")
        self.ledger.process_quotes([{**base, "volume": 1, "time": "2026-09-21T12:00:00"}], dt.datetime(2026, 9, 21, 12, 1))
        self.assertEqual(self.ledger.summary(dt.datetime(2026, 9, 21, 12, 1))["orders"][0]["reason"], "outside_trading_session")

    def test_expiration_releases_reservation_and_cancellation_is_safe(self):
        self.ledger.ingest([self.recommendation(expires_at="2026-09-21T10:02:00")])
        order = self.ledger.create_order("rec-1", self.when)
        self.assertEqual(self.ledger.create_order("rec-1", dt.datetime(2026, 9, 21, 10, 3))["status"], "expired")
        expired = self.ledger.summary(dt.datetime(2026, 9, 21, 10, 3))["orders"][0]
        self.assertEqual((expired["status"], expired["reserved_cash"]), ("expired", "0"))
        self.ledger.ingest([self.recommendation(id="rec-2")])
        pending = self.ledger.create_order("rec-2", self.when)
        self.assertEqual(self.ledger.cancel_order(pending["id"])["status"], "cancelled")

    def test_shared_ledger_serializes_concurrent_idempotent_ingest(self):
        outcomes = []
        def run():
            outcomes.append(self.ledger.ingest([self.recommendation()]))
        threads = [threading.Thread(target=run) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads: thread.join()
        self.assertEqual(sum(item["inserted"] for item in outcomes), 1)

    def test_ledger_directory_and_file_are_private_for_new_and_existing_records(self):
        ledger_dir = Path(self.tmp.name) / "private-ledger"
        path = ledger_dir / "paper.sqlite3"
        first = Ledger(path)
        first.ingest([self.recommendation()])
        first.close()
        os.chmod(ledger_dir, 0o755)
        os.chmod(path, 0o644)
        reopened = Ledger(path)
        self.assertEqual(stat.S_IMODE(ledger_dir.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(reopened.summary(self.when)["recommendations"][0]["id"], "rec-1")
        reopened.close()

    def test_close_rejects_mismatched_or_stale_snapshot(self):
        self.ledger.ingest([self.recommendation(recommended_at="2026-09-18T10:00:00")])
        self.ledger.create_order("rec-1", dt.datetime(2026, 9, 18, 10, 0))
        self.ledger.process_quotes([{
            "code": "sz000001", "time": "2026-09-18T10:01:00", "price": "9.9", "volume": 1,
            "limit_up": "11", "limit_down": "9",
        }], dt.datetime(2026, 9, 18, 10, 2))
        wrong = {"code": "sh600000", "time": "2026-09-21T10:00:00", "price": "9.8", "volume": 1, "limit_up": "11", "limit_down": "8.9"}
        self.assertEqual(self.ledger.close_position("sz000001", wrong, dt.datetime(2026, 9, 21, 10, 1))["reason"], "quote_code_mismatch")
        stale = {**wrong, "code": "sz000001"}
        self.assertEqual(self.ledger.close_position("sz000001", stale, dt.datetime(2026, 9, 21, 10, 4))["reason"], "stale_quote")


if __name__ == "__main__":
    unittest.main()
