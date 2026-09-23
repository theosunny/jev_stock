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

    def test_three_sections_separate_research_from_purchase(self):
        record = cycle.read_record(self.root, self.run_once()['report_id'])
        message = record['message']
        for title in ('一、市场分析', '二、买入建议', '三、回测验证'):
            self.assertIn(title, message)
        self.assertIn('本轮不建议新买入', message)
        self.assertNotIn('约5000', message)
        self.assertNotIn('5000元', message)
        self.assertEqual(record['backtest_validation']['status'], 'not_backtested')
        self.assertEqual(record['backtest_validation']['records'], 0)

    def test_research_board_pattern_not_presented_as_buy_candidate(self):
        self.decision['stocks'][0].update(action='观察', research_only=True, entry_type='排板候选')
        message = cycle.read_record(self.root, self.run_once()['report_id'])['message']
        self.assertIn('形态证据: 排板', message)
        self.assertNotIn('参与方式: 排板候选', message)
        self.assertIn('本轮不建议新买入', message)

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
        for ch in ('feishu', 'slack'): cycle.ack(self.root, rid, ch, 'https://example.slack.com/archives/D123/p1234567890000000', self.now)
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

class JevNetworkTests(unittest.TestCase):
    def test_fresh_snapshot_is_sent_and_valid_response_required(self):
        import io
        snapshot = {'as_of': '2026-09-22T14:00:00', 'sector_counts': {'医药': 4}}
        plan = {'watchlist': [{'code': 'sh600664'}]}
        questions = live_jev.build_questions(snapshot, plan)
        result = {'model': 'jev-test', 'answers': {key: {'type': 'choice', 'choice': next(iter(q['criteria'])), 'confidence': .8} for key, q in questions.items()}}
        with patch.object(live_jev.jev_analyze, 'load_env', return_value={'TYPESAFE_API_KEY': 'test-only'}), patch.object(live_jev.urllib.request, 'urlopen', return_value=io.BytesIO(json.dumps(result).encode())) as call:
            self.assertEqual(live_jev.analyze(snapshot, plan, {}), result)
            payload = json.loads(call.call_args.args[0].data)
            self.assertEqual(payload['state']['snapshot']['as_of'], snapshot['as_of'])
            self.assertIn('action_sh600664', payload['questions'])
        with patch.object(live_jev.jev_analyze, 'load_env', return_value={}):
            with self.assertRaises(ValueError): live_jev.analyze(snapshot, plan, {})

    def test_nonfinite_confidence_and_missing_answers_fail(self):
        questions = {'m': {'criteria': {'观察': ''}}}
        for confidence in (float('nan'), -1, 2, 'x'):
            with self.assertRaises(ValueError):
                live_jev.validate_result({'answers': {'m': {'type': 'choice', 'choice': '观察', 'confidence': confidence}}}, questions)
        with self.assertRaises(ValueError): live_jev.validate_result({}, questions)


class RecoveryTests(unittest.TestCase):
    setUp = CycleTests.setUp
    tearDown = CycleTests.tearDown
    run_once = CycleTests.run_once

    def test_malformed_rule_fields_are_rejected_before_evaluation(self):
        bad_rules = [{'buy_band_pct': 'oops'}, {'confirm': 'oops'},
                     {'confirm': {'open_pct_range': [5, 2]}},
                     {'confirm': {'low_open': {'open_pct_range': [0, 'bad']}}},
                     {'confirm': {'price_above_open_pct': float('nan')}},
                     {'confirm': {'after_time': '25:00'}}]
        for rule in bad_rules:
            with self.subTest(rule=rule), self.assertRaises(ValueError):
                cycle.validate_plan({'watchlist': [{'code': 'sz000560', **rule}]})

    def test_ebb_persists_until_all_recovery_conditions(self):
        self.run_once()
        state = cycle.load(cycle.folder(self.root) / 'state.json')
        self.assertTrue(state['reentry_required'])
        self.decision = {**self.decision, 'buy_allowed': True, 'risk_reasons': [], 'reentry_confirmed': True,
                         'stocks': [{**self.decision['stocks'][0], 'action': '买入候选'}]}
        self.snapshot = {**self.snapshot, 'emotion': {**self.snapshot['emotion'], 'zt': 51, 'zb_rate': 27}}
        # Pure evaluator is stubbed; recovery also requires its full confirmation.
        self.decision['reentry_confirmed'] = False
        record = cycle.read_record(self.root, self.run_once(self.now + dt.timedelta(minutes=5))['report_id'])
        self.assertFalse(record['decision']['buy_allowed'])
        self.decision['reentry_confirmed'] = True
        self.snapshot['emotion']['zb_rate'] = 20
        record = cycle.read_record(self.root, self.run_once(self.now + dt.timedelta(minutes=10))['report_id'])
        self.assertTrue(record['decision']['buy_allowed'])
        self.assertFalse(cycle.load(cycle.folder(self.root) / 'state.json')['reentry_required'])

    def test_legacy_ebb_state_is_carried_over(self):
        (self.root / '.alert_state.json').write_text(json.dumps({'market:ebb_note': '2026-09-22'}))
        self.decision = {**self.decision, 'buy_allowed': True, 'risk_reasons': []}
        result = self.run_once()
        self.assertFalse(cycle.read_record(self.root, result['report_id'])['decision']['buy_allowed'])

    def test_ebb_baseline_uses_previous_emotion_before_ten_and_is_not_rebased(self):
        first = self.now.replace(hour=9, minute=30)
        self.snapshot = {
            **self.snapshot,
            'as_of': first.isoformat(),
            'emotion': {**self.snapshot['emotion'], 'zt': 5, 'zb_rate': 10.0},
            'previous_emotion': {'zt': 40, 'zb': 15, 'dt': 3, 'zb_rate': 30.0, 'max_lb': 5},
        }
        self.run_once(first)
        state = cycle.load(cycle.folder(self.root) / 'state.json')
        self.assertEqual(state['ebb_baseline'], {'zt': 40, 'zb_rate': 30.0})

        self.snapshot = {
            **self.snapshot,
            'as_of': (first + dt.timedelta(minutes=5)).isoformat(),
            'emotion': {**self.snapshot['emotion'], 'zt': 8, 'zb_rate': 26.0},
            'previous_emotion': {'zt': 45, 'zb': 20, 'dt': 4, 'zb_rate': 35.0, 'max_lb': 4},
        }
        self.run_once(first + dt.timedelta(minutes=5))
        state = cycle.load(cycle.folder(self.root) / 'state.json')
        self.assertEqual(state['ebb_baseline'], {'zt': 40, 'zb_rate': 30.0})

    def test_evaluator_failure_writes_a_fail_closed_error_report(self):
        def broken_evaluator(_snapshot, _plan):
            raise ValueError('private evaluator detail')

        result = cycle.run_cycle(
            self.root, self.now,
            collector=lambda _plan, _now: self.snapshot,
            evaluator=broken_evaluator,
            analyzer=lambda *_args: self.jev,
        )

        record = cycle.read_record(self.root, result['report_id'])
        self.assertEqual(record['status'], 'error')
        self.assertFalse(record['decision']['buy_allowed'])
        self.assertEqual(record['decision']['stocks'][0]['action'], '暂停买入')
        self.assertNotIn('private evaluator detail', record['message'])

    def test_enrichment_failure_writes_a_fail_closed_error_report(self):
        decision = {**self.decision, 'buy_allowed': True, 'risk_reasons': [], 'stocks': [
            {**self.decision['stocks'][0], 'action': '买入候选'}
        ]}
        with patch.object(cycle, 'enrich', side_effect=ValueError('private enrichment detail')):
            result = cycle.run_cycle(
                self.root, self.now,
                collector=lambda _plan, _now: self.snapshot,
                evaluator=lambda _snapshot, _plan: decision,
                analyzer=lambda *_args: self.jev,
            )

        record = cycle.read_record(self.root, result['report_id'])
        self.assertEqual(record['status'], 'error')
        self.assertFalse(record['decision']['buy_allowed'])
        self.assertEqual(record['decision']['stocks'][0]['action'], '暂停买入')
        self.assertNotIn('private enrichment detail', record['message'])

    def test_market_failure_preserves_partial_position_snapshot(self):
        import live_analysis
        self.decision['stocks'][0]['action'] = '止损提醒'
        def failed(plan, now): raise live_analysis.SnapshotError('unavailable', self.snapshot)
        result = cycle.run_cycle(self.root, self.now, collector=failed,
                                 evaluator=lambda s, p: self.decision, analyzer=lambda *a: self.jev)
        record = cycle.read_record(self.root, result['report_id'])
        self.assertEqual(record['status'], 'error')
        self.assertEqual(record['decision']['stocks'][0]['action'], '止损提醒')

    def test_nontrading_day_and_missing_quotes(self):
        import live_analysis
        def closed(plan, now): raise live_analysis.NonTradingDay('holiday')
        self.assertEqual(cycle.run_cycle(self.root, self.now, collector=closed)['status'], 'skipped')
        def failed(plan, now): raise live_analysis.SnapshotError('network')
        result = cycle.run_cycle(self.root, self.now, collector=failed)
        self.assertEqual(result['status'], 'error')
        self.assertFalse(cycle.read_record(self.root, result['report_id'])['decision']['buy_allowed'])

class BoardEntryCycleTests(unittest.TestCase):
    setUp = CycleTests.setUp
    tearDown = CycleTests.tearDown
    run_once = CycleTests.run_once
    def board_decision(self):
        self.decision = {'buy_allowed': True, 'risk_reasons': [], 'stocks': [
            {'code': 'sz000560', 'name': '核心', 'action': '买入候选', 'reason': '待人工盘口复核',
             'price': 3.3, 'entry_type': '排板候选', 'industry': '房地产服务'}]}
        self.jev['answers'].update({'market': {'choice': '分歧'}, 'mainline': {'choice': '房地产服'},
                                   'core_sz000560': {'choice': '主线核心'},
                                   'alignment_sz000560': {'choice': '匹配主线'}})

    def test_board_requires_core_alignment_and_action(self):
        self.board_decision()
        self.assertEqual(cycle.combine(self.decision, self.jev, None)['stocks'][0]['action'], '买入候选')
        for key, value in [('core_sz000560','证据不足'), ('alignment_sz000560','不匹配'),
                           ('mainline','无明确主线'), ('market','退潮'), ('market','高潮'), ('action_sz000560','观察')]:
            altered = {**self.jev, 'answers': {**self.jev['answers'], key: {'choice': value}}}
            self.assertEqual(cycle.combine(self.decision, altered, None)['stocks'][0]['action'], '观察')
        self.assertEqual(cycle.combine(self.decision, self.jev, 'HTTPError')['stocks'][0]['action'], '暂停买入')

    def test_research_has_no_implicit_budget(self):
        d = {'stocks':[{'code':'sh600001','name':'研究','price':10,'action':'观察','research_only':True}]}
        row = cycle.enrich(d, {}, self.plan)['stocks'][0]
        self.assertFalse(row.get('budget'))
        self.assertFalse(row.get('shares'))

    def test_board_entry_change_is_not_suppressed(self):
        self.board_decision()
        first = self.run_once()
        for ch in ('feishu','slack'):
            cycle.ack(self.root, first['report_id'], ch, '1790131686.843549', self.now)
        self.decision['stocks'][0]['entry_type'] = '回封候选'
        second = self.run_once(self.now + dt.timedelta(minutes=5))
        self.assertEqual(second['pending_channels'], ['feishu','slack'])
        record = cycle.read_record(self.root, second['report_id'])
        self.assertIn('回封候选', record['message'])
        self.assertIn('人工盘口复核', record['message'])
        self.assertEqual(dt.datetime.fromisoformat(record['expires_at']) - (self.now+dt.timedelta(minutes=5)),dt.timedelta(minutes=3))

    def test_previous_successful_snapshot_is_injected_but_stale_is_not(self):
        self.snapshot['limit_pool'] = [{'code':'sz000560','fund':100}]
        self.run_once()
        seen=[]
        def evaluate(s,p):seen.append(s);return self.decision
        cycle.run_cycle(self.root,self.now+dt.timedelta(minutes=5),collector=lambda p,n:self.snapshot,
                        evaluator=evaluate,analyzer=lambda s,p,d:self.jev)
        self.assertEqual(seen[-1]['previous_limit_pool'],self.snapshot['limit_pool'])
        cycle.run_cycle(self.root,self.now+dt.timedelta(minutes=25),collector=lambda p,n:self.snapshot,
                        evaluator=evaluate,analyzer=lambda s,p,d:self.jev)
        self.assertFalse(seen[-1].get('previous_limit_pool'))

    def test_failed_previous_cycle_cannot_supply_board_evidence(self):
        self.snapshot['limit_pool'] = [{'code':'sz000560','fund':100}]
        def fail(*args): raise RuntimeError('offline')
        self.run_once(analyze=fail)
        state=cycle.load(cycle.folder(self.root)/'state.json')
        self.assertFalse(cycle.previous_board_evidence(self.root,state,self.now+dt.timedelta(minutes=5))['previous_limit_pool'])

    def test_new_research_questions_are_bounded_to_evaluated_symbols(self):
        s={'sector_counts':{'医药':4},'limit_pool':[{'code':'sh600001'},{'code':'sh600002'}],
           'board_quotes':{'sh600001':{},'sh600002':{}}}
        q=live_jev.build_questions(s,self.plan,{'stocks':[{'code':'sh600001'}]})
        self.assertIn('core_sh600001',q)
        self.assertIn('alignment_sh600001',q)
        self.assertNotIn('core_sh600002',q)

    def test_board_candidate_is_not_allowed_outside_continuous_session(self):
        self.board_decision()
        record=cycle.read_record(self.root,self.run_once(self.now.replace(hour=15,minute=5))['report_id'])
        self.assertEqual(record['decision']['stocks'][0]['action'],'观察')
        self.assertFalse(record['decision']['buy_allowed'])
