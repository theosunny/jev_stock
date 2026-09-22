import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'stock-signal/scripts'))
import codex_cycle as cycle
import live_jev


class CycleTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = dt.datetime(2026, 9, 22, 14, 0)
        self.plan = {'总资金': 100000, 'watchlist': [{'code': 'sz000560', 'name': '我爱我家', 'enabled': True}]}
        (self.root / 'plan.json').write_text(json.dumps(self.plan))
        self.snapshot = {'as_of': self.now.isoformat(), 'quotes': {'sz000560': {'price': 3.3, 'time': '20260922140000'}}, 'emotion': {'zt': 50, 'zb': 20, 'dt': 3, 'zb_rate': 28.6, 'max_lb': 6}, 'warnings': []}
        self.decision = {'buy_allowed': False, 'risk_reasons': ['炸板率超过25%'], 'stocks': [{'code': 'sz000560', 'name': '我爱我家', 'action': '暂停买入', 'reason': '退潮', 'price': 3.3}]}
        self.jev = {'model': 'test', 'answers': {'market': {'choice': '分歧'}, 'mainline': {'choice': '无明确主线'}, 'action_sz000560': {'choice': '买入候选'}, 'reason_sz000560': {'choice': '情绪风险'}}}

    def tearDown(self):
        self.tmp.cleanup()

    def run_once(self, now=None, force=False, analyze=None):
        return cycle.run_cycle(self.root, now or self.now, force_report=force,
            collector=lambda p, n: self.snapshot, evaluator=lambda s, p: self.decision,
            analyzer=analyze or (lambda s, p, d: self.jev))

    def test_model_cannot_override_hard_pause_and_no_trade_log(self):
        result = self.run_once()
        record = cycle.read_record(self.root, result['report_id'])
        self.assertEqual(record['decision']['stocks'][0]['action'], '暂停买入')
        self.assertEqual(result['pending_channels'], ['feishu', 'slack'])
        self.assertFalse((self.root / 'trade_log.jsonl').exists())

    def test_same_slot_does_not_call_model_twice(self):
        first = self.run_once()
        second = self.run_once(analyze=lambda *args: self.fail('duplicate Jev call'))
        self.assertEqual(first['report_id'], second['report_id'])

    def test_ack_is_per_channel_and_idempotent(self):
        rid = self.run_once()['report_id']
        cycle.ack(self.root, rid, 'feishu', 'confirmed', self.now)
        cycle.ack(self.root, rid, 'feishu', 'another', self.now)
        record = cycle.read_record(self.root, rid)
        self.assertEqual(record['channels']['feishu']['receipt'], 'confirmed')
        self.assertEqual(cycle.pending(self.root, self.now)[0]['pending_channels'], ['slack'])

    def test_failed_model_suppresses_buy_but_retains_stop(self):
        self.decision = {**self.decision, 'buy_allowed': True, 'stocks': [
            {'code': 'sz000560', 'name': '样例', 'action': '买入候选', 'reason': '符合', 'price': 3},
            {'code': 'sh600664', 'name': '持仓', 'action': '止损提醒', 'reason': '跌破止损', 'price': 8}]}
        def failed(*args): raise RuntimeError('secret should not leak')
        result = self.run_once(analyze=failed)
        record = cycle.read_record(self.root, result['report_id'])
        self.assertFalse(record['decision']['buy_allowed'])
        self.assertEqual([s['action'] for s in record['decision']['stocks']], ['暂停买入', '止损提醒'])
        self.assertNotIn('secret should not leak', record['message'])

    def test_model_vetoes_candidate(self):
        self.decision = {**self.decision, 'buy_allowed': True, 'stocks': [{**self.decision['stocks'][0], 'action': '买入候选'}]}
        self.jev['answers']['action_sz000560']['choice'] = '观察'
        record = cycle.read_record(self.root, self.run_once()['report_id'])
        self.assertEqual(record['decision']['stocks'][0]['action'], '观察')

    def test_identical_successfully_delivered_state_stays_quiet(self):
        rid = self.run_once()['report_id']
        for ch in ('feishu', 'slack'): cycle.ack(self.root, rid, ch, 'receipt', self.now)
        result = self.run_once(self.now + dt.timedelta(minutes=5))
        self.assertEqual(result['pending_channels'], [])

    def test_failed_channel_is_not_suppressed_next_cycle(self):
        rid = self.run_once()['report_id']
        cycle.ack(self.root, rid, 'feishu', 'receipt', self.now)
        result = self.run_once(self.now + dt.timedelta(minutes=5))
        self.assertEqual(result['pending_channels'], ['slack'])

    def test_expired_buy_report_not_retried(self):
        self.decision = {**self.decision, 'buy_allowed': True, 'stocks': [{**self.decision['stocks'][0], 'action': '买入候选'}]}
        self.run_once()
        self.assertEqual(cycle.pending(self.root, self.now + dt.timedelta(minutes=6)), [])

    def test_outside_market_hours_no_network(self):
        result = self.run_once(self.now.replace(hour=20), analyze=lambda *a: self.fail('after hours'))
        self.assertEqual(result['status'], 'skipped')

    def test_lock_prevents_overlapping_cycles(self):
        with cycle.lock(self.root):
            result = self.run_once(analyze=lambda *a: self.fail('overlap'))
        self.assertEqual(result['status'], 'busy')

    def test_corrupt_state_is_not_silently_reset(self):
        folder = self.root / 'codex_monitor'
        folder.mkdir()
        (folder / 'state.json').write_text('{broken')
        with self.assertRaises(ValueError): self.run_once()

    def test_invalid_ids_and_receipts_rejected(self):
        with self.assertRaises(ValueError): cycle.read_record(self.root, '../plan')
        rid = self.run_once()['report_id']
        with self.assertRaises(ValueError): cycle.ack(self.root, rid, 'slack', '', self.now)


class JevTests(unittest.TestCase):
    def test_dynamic_questions_cover_every_enabled_symbol(self):
        plan = {'watchlist': [{'code': 'sz000560', 'enabled': True}, {'code': 'sh600664', 'enabled': False}]}
        questions = live_jev.build_questions({'sector_counts': {'医药': 4}}, plan)
        self.assertIn('action_sz000560', questions)
        self.assertIn('reason_sz000560', questions)
        self.assertNotIn('action_sh600664', questions)
        self.assertIn('market', questions)
        self.assertIn('mainline', questions)

    def test_partial_or_unknown_answer_is_failure(self):
        questions = {'market': {'type': 'choice', 'criteria': {'分歧': ''}}}
        for result in ({'answers': {}}, {'answers': {'market': {'type': 'choice', 'choice': '乱猜'}}}):
            with self.assertRaises(ValueError): live_jev.validate_result(result, questions)

if __name__ == '__main__': unittest.main()
