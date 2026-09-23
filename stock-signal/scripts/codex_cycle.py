#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Codex heartbeat: fresh Jev analysis, hard risk gates and per-channel receipts.

Never calls the paper/real execution layer. Production data lives outside the skill.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import tempfile
from zoneinfo import ZoneInfo

import datadir
import live_jev
import strategy_identity
from report_sections import purchase_section, history_coverage, backtest_section

CHANNELS = ('feishu', 'slack')


def now_cn():
    return dt.datetime.now(ZoneInfo('Asia/Shanghai')).replace(tzinfo=None)


def folder(root):
    result = Path(root) / 'codex_monitor'
    result.mkdir(parents=True, exist_ok=True)
    result.chmod(0o700)
    return result


def load(path, default=None):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except (ValueError, OSError) as exc:
        raise ValueError('Unreadable state: ' + str(path.name)) from exc


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=path.parent, delete=False) as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        tmp = stream.name
    os.replace(tmp, path)


@contextmanager
def lock(root):
    with (folder(root) / '.lock').open('a') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            yield
        finally:
            fcntl.flock(stream, fcntl.LOCK_UN)


def session(now):
    if now.weekday() >= 5:
        return None
    t = now.time()
    if dt.time(9, 25) <= t < dt.time(9, 30):
        return 'heartbeat'
    if dt.time(9, 15) <= t < dt.time(9, 25):
        return 'auction'
    if dt.time(9, 30) <= t <= dt.time(11, 30) or dt.time(13) <= t < dt.time(15):
        return 'intraday'
    if dt.time(15) <= t <= dt.time(15, 20):
        return 'close'
    return None


def finite_number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_confirm(config):
    if not isinstance(config, dict):
        raise ValueError('invalid confirmation rules')
    if 'open_pct_range' in config:
        values = config['open_pct_range']
        if (not isinstance(values, list) or len(values) != 2
                or not all(finite_number(x) for x in values) or values[0] > values[1]):
            raise ValueError('invalid opening range')
    if 'price_above_open_pct' in config and not finite_number(config['price_above_open_pct']):
        raise ValueError('invalid confirmation threshold')
    if 'after_time' in config:
        value = config['after_time']
        if not isinstance(value, str) or not re.fullmatch(r'\d{2}:\d{2}', value):
            raise ValueError('invalid confirmation time')
        dt.time.fromisoformat(value)
    if 'low_open' in config:
        validate_confirm(config['low_open'])


def validate_plan(plan):
    if not isinstance(plan, dict) or not isinstance(plan.get('watchlist'), list):
        raise ValueError('invalid plan/watchlist')
    if not plan['watchlist'] or len(plan['watchlist']) > 50:
        raise ValueError('watchlist must contain 1..50 stocks')
    for key, default in (('总资金', 100000), ('总仓位上限pct', 30)):
        value = plan.get(key, default)
        if not finite_number(value) or value <= 0:
            raise ValueError('invalid account limits')
    seen = set()
    for stock in plan['watchlist']:
        code = stock.get('code') if isinstance(stock, dict) else None
        if not isinstance(code, str) or not re.fullmatch(r'(sh|sz|bj)\d{6}', code) or code in seen:
            raise ValueError('invalid or duplicate stock code')
        seen.add(code)
        for key in ('buy_size_pct', 'ref_price', 'stop_loss_pct', 'buy_band_pct'):
            value = stock.get(key)
            if value is not None and (not finite_number(value) or value <= 0):
                raise ValueError('invalid stock position values')
        if 'confirm' in stock:
            validate_confirm(stock['confirm'])
    return plan


def record_path(root, report_id):
    if not re.fullmatch(r'[a-zA-Z0-9_-]{1,100}', report_id):
        raise ValueError('invalid report id')
    return folder(root) / 'reports' / (report_id + '.json')


def read_record(root, report_id):
    result = load(record_path(root, report_id))
    if not isinstance(result, dict):
        raise ValueError('unknown report id')
    return result


def summary(record, now):
    fresh = now <= dt.datetime.fromisoformat(record['expires_at'])
    channels = [name for name in CHANNELS if record['channels'][name]['status'] == 'pending'] if fresh else []
    return {'status': record['status'], 'report_id': record['report_id'],
            'message_path': record['message_path'], 'report_path': record['report_path'],
            'pending_channels': channels, 'expires_at': record['expires_at']}


def pending(root, now=None):
    now = now or now_cn()
    state = load(folder(root) / 'state.json', {})
    latest = state.get('latest_id')
    items = []
    for channel, rid in state.get('unconfirmed', {}).items():
        item = summary(read_record(root, rid), now)
        items.append({**item, 'status': 'needs_verification', 'verify_channel': channel})
    if latest:
        result = summary(read_record(root, latest), now)
        if result['pending_channels']:
            items.append(result)
    return items


def _ack_locked(root, report_id, channel, receipt, now):
    record = read_record(root, report_id)
    if record['channels'][channel]['status'] not in ('pending', 'sending', 'delivered'):
        raise ValueError('channel is not awaiting delivery')
    if record['channels'][channel]['status'] != 'delivered':
        record['channels'][channel] = {'status': 'delivered', 'receipt': receipt, 'delivered_at': now.isoformat()}
        write(record_path(root, report_id), record)
    state_path = folder(root) / 'state.json'
    state = load(state_path, {})
    delivered = dict(state.get('delivered', {}))
    delivered_at = dict(state.get('delivered_at', {}))
    if now <= dt.datetime.fromisoformat(record['expires_at']) and record['created_at'] >= delivered_at.get(channel, ''):
        delivered[channel] = record['fingerprint']
        delivered_at[channel] = record['created_at']
    unconfirmed = {k: v for k, v in state.get('unconfirmed', {}).items() if not (k == channel and v == report_id)}
    write(state_path, {**state, 'delivered': delivered, 'delivered_at': delivered_at, 'unconfirmed': unconfirmed})
    return summary(record, now)


def ack(root, report_id, channel, receipt, now=None):
    if channel not in CHANNELS or not isinstance(receipt, str) or not receipt.strip():
        raise ValueError('channel and a verified receipt are required')
    if channel == 'slack' and not re.fullmatch(r'(https://[^/]+\.slack\.com/archives/[A-Z0-9]+/p\d+|\d{10}\.\d+)', receipt):
        raise ValueError('Slack receipt must be a verified permalink or message timestamp')
    now = now or now_cn()
    with lock(root):
        return _ack_locked(root, report_id, channel, receipt, now)


def _claim_locked(root, report_id, channel, now):
    state_path = folder(root) / 'state.json'
    state = load(state_path, {})
    outstanding = state.get('unconfirmed', {}).get(channel)
    if outstanding:
        return {'status': 'needs_verification', 'report_id': outstanding, 'verify_channel': channel}
    record = read_record(root, report_id)
    if channel not in summary(record, now)['pending_channels']:
        return {'status': 'skipped', 'report_id': report_id}
    # Persist uncertainty BEFORE the external call. A crash requires read-back,
    # never an automatic resend under a new report id.
    write(state_path, {**state, 'unconfirmed': {**state.get('unconfirmed', {}), channel: report_id}})
    record['channels'][channel] = {'status': 'sending', 'started_at': now.isoformat()}
    write(record_path(root, report_id), record)
    return {**summary(record, now), 'status': 'ready_to_send'}


def claim(root, report_id, channel, now=None):
    if channel not in CHANNELS:
        raise ValueError('unknown channel')
    with lock(root):
        return _claim_locked(root, report_id, channel, now or now_cn())


def release(root, report_id, channel, receipt, now=None):
    """Recover a claimed message ONLY after the agent verifies it was not sent."""
    if channel not in CHANNELS or not receipt.startswith('verified_absent:') or len(receipt) < 25:
        raise ValueError('record the negative delivery verification before retrying')
    now = now or now_cn()
    with lock(root):
        state_path = folder(root) / 'state.json'
        state = load(state_path, {})
        if state.get('unconfirmed', {}).get(channel) != report_id:
            raise ValueError('no matching unconfirmed delivery')
        record = read_record(root, report_id)
        attempts = record.get('release_count', {}).get(channel, 0)
        if attempts >= 2:
            raise ValueError('retry limit reached; user action required')
        record['release_count'] = {**record.get('release_count', {}), channel: attempts + 1}
        record['channels'][channel] = {'status': 'pending', 'negative_verification': receipt}
        write(record_path(root, report_id), record)
        state = {**state, 'unconfirmed': {k: v for k, v in state['unconfirmed'].items() if k != channel}}
        latest = state.get('latest_id')
        if latest and latest != report_id:
            current = read_record(root, latest)
            if current['channels'][channel]['status'] == 'blocked':
                current['channels'][channel] = {'status': 'pending'}
                write(record_path(root, latest), current)
        write(state_path, state)
        return {'status': 'released', 'pending': pending(root, now)}


def combine(decision, jev, error):
    answers = jev.get('answers', {}) if jev else {}
    stocks = []
    for stock in decision['stocks']:
        item = dict(stock)
        opinion = answers.get('action_' + stock['code'], {}).get('choice', '数据不足')
        item['jev_action'] = opinion
        item['jev_reason'] = answers.get('reason_' + stock['code'], {}).get('choice', '数据不足')
        if stock['action'] == '买入候选' and (error or opinion != '买入候选'):
            item.update(action='暂停买入' if error else '观察', reason=stock['reason'] + '；Jev未确认买入候选')
        if item['action'] == '买入候选' and stock.get('entry_type'):
            mainline = answers.get('mainline', {}).get('choice', '')
            industry = stock.get('industry') or ''
            aligned = (isinstance(mainline, str) and isinstance(industry, str)
                       and len(mainline) >= 3 and len(industry) >= 3
                       and (mainline.startswith(industry) or industry.startswith(mainline)))
            core = answers.get('core_' + stock['code'], {}).get('choice')
            match = answers.get('alignment_' + stock['code'], {}).get('choice')
            market = answers.get('market', {}).get('choice')
            if (not aligned or core != '主线核心' or match != '匹配主线'
                    or market not in ('回暖', '发酵', '分歧')):
                item.update(action='观察', reason=item['reason'] + '；Jev未同时确认主线匹配、核心地位与情绪条件')
        stocks.append(item)
    reasons = list(decision['risk_reasons']) + (['Jev或数据异常，暂停新买入'] if error else [])
    return {**decision, 'buy_allowed': decision['buy_allowed'] and not error, 'risk_reasons': reasons, 'stocks': stocks}


def make_message(report_id, snapshot, decision, jev, status, session_name, coverage=None):
    em = snapshot.get('emotion', {})
    answers = jev.get('answers', {}) if jev else {}
    market = answers.get('market', {}).get('choice', '数据不足')
    mainline = answers.get('mainline', {}).get('choice', '无明确主线')
    lines = ['**A股%s分析 | %s**' % ({'heartbeat': '心跳', 'auction': '竞价', 'close': '收盘', 'intraday': '盘中'}[session_name], snapshot['as_of']),
             '报告编号: ' + report_id,
             '**一、市场分析**',
             'Jev: %s；观察主线: %s；模型: %s' % (market, mainline, (jev or {}).get('model', '本轮不可用')),
             '硬风控: ' + ('允许进一步核对个股候选，非下单指令' if decision['buy_allowed'] else '暂停新开仓'),
             '风险依据: ' + ('；'.join(decision['risk_reasons']) or '本轮未触发市场级闸门')]
    position = decision.get('position', {})
    if position:
        lines.append('登记仓位: 已用%s%% / 上限%s%%；总资金%s元（以实际账户为准）' % (position['used_pct'], position['cap_pct'], position['capital']))
    if em:
        lines.append('涨停%s / 炸板%s（%s%%）/ 跌停%s / 最高%s板' % tuple(em.get(k, '?') for k in ('zt', 'zb', 'zb_rate', 'dt', 'max_lb')))
    for code in ('sh000001', 'sz399001', 'sz399006'):
        q = snapshot.get('quotes', {}).get(code)
        if q:
            lines.append('%s: %.2f (%+.2f%%)，行情时间%s' % (q['name'], q['price'], q['pct'], q['time']))
    lines.append('\n**逐股判断**')
    for stock in decision['stocks']:
        lines.append('- %s %s: %s | %s\n  %s\n  Jev: %s / %s' % (
            stock['name'], stock['code'], stock.get('price', '无行情'), stock['action'], stock['reason'], stock['jev_action'], stock['jev_reason']))
        if stock.get('entry_type'):
            lines.append('  形态证据: ' + stock['entry_type'].removesuffix('候选') + '；是否建议参与见买入建议栏')
        if stock.get('notice_risks'):
            lines.append('  公告风险关键词: ' + '；'.join(stock['notice_risks'])[:180])
    if session_name in ('auction', 'heartbeat') and snapshot.get('overnight'):
        lines.append('\n' + snapshot['overnight'])
    news = snapshot.get('news', [])
    if news:
        lines.append('\n**本轮快讯（供核验，不替代价格与风控）**')
        lines += ['- %s %s' % (n.get('time', ''), n.get('text', '')[:160]) for n in news[:3]]
    if snapshot.get('warnings'):
        lines.append('数据提示: ' + '；'.join(snapshot['warnings'])[:250])
    if status == 'error':
        lines.append('本轮分析未完整成功，买入保持暂停；请查看本地报告error_type。')
    lines += purchase_section(decision, snapshot['as_of'], status, session_name)
    lines += backtest_section(coverage)
    lines += ['\n规则：陈小群主线龙头、买分歧卖一致；小鳄鱼仓位优先、弱转强确认。',
              '首测试错0.5成、单票不超过2成；-6%触发止损提醒，须核实实际成本、T+1可卖数量及流动性。',
              '仅分析提醒，不自动买卖或登记持仓。来源：腾讯行情、东方财富情绪池/快讯；原始快照及Jev结果保存在本地报告。',
              '技术研究辅助，不构成投资建议。']
    return '\n'.join(lines)


def enrich(decision, snapshot, plan):
    capital = plan.get('总资金', 100000)
    configured = {s['code']: s for s in plan['watchlist']}
    stocks = []
    for stock in decision['stocks']:
        if stock.get('research_only') or stock['code'] not in configured:
            stocks.append({**stock, 'budget': 0, 'shares': 0, 'buy_reference': '研究观察：未配置交易仓位'})
            continue
        config = configured.get(stock['code'], {})
        pct = config.get('buy_size_pct', 5)
        budget = round(capital * pct / 100, 2)
        price = stock.get('price') or 0
        ma5 = snapshot.get('ma5', {}).get(stock['code'])
        reference = '弱转强须满足计划开盘幅度/时间与VWAP，人工确认持续性'
        if config.get('buy_anchor') == 'ma5' and ma5:
            band = config.get('buy_band_pct', 1) / 100
            reference = 'MA5参考区间%.2f–%.2f' % (ma5 * (1-band), ma5 * (1+band))
        if stock.get('entry_type'):
            reference = stock['entry_type'] + '：以当日涨停价为参考，先核对即时盘口、排队和撤单风险；不保证成交'
        stocks.append({**stock, 'size_pct': pct, 'budget': budget, 'shares': int(budget // (price * 100)) * 100 if price else 0,
                       'buy_reference': reference, 'notice_risks': snapshot.get('announcements', {}).get(stock['code'], {}).get('risk', [])})
    return {**decision, 'stocks': stocks, 'position': {'capital': capital, 'cap_pct': plan.get('总仓位上限pct', 30),
            'used_pct': sum(s.get('buy_size_pct', 0) for s in plan['watchlist'] if s.get('ref_price'))}}


def failed_decision(plan):
    return {'buy_allowed': False, 'risk_reasons': ['规则计算异常，暂停新买入；持仓需人工核查'],
            'stocks': [{'code': s['code'], 'name': s.get('name', s['code']),
                        'action': '暂停买入', 'reason': '规则计算异常，不能确认交易条件'}
                       for s in plan['watchlist'] if s.get('enabled', True)]}


def previous_board_evidence(root, state, now):
    """Use only the immediately preceding successful, recent same-day snapshot."""
    empty = {'previous_limit_pool': [], 'previous_board_quotes': {}, 'previous_board_as_of': None}
    latest = state.get('latest_id')
    if not latest:
        return empty
    record = read_record(root, latest)
    if record.get('status') != 'success':
        return empty
    previous = record.get('snapshot') or {}
    try:
        at = dt.datetime.fromisoformat(previous['as_of'])
        recent = at.date() == now.date() and 0 < (now - at).total_seconds() <= 600
    except (KeyError, TypeError, ValueError):
        return empty
    if not recent or not isinstance(previous.get('limit_pool'), list):
        return empty
    return {'previous_limit_pool': previous['limit_pool'], 'previous_board_as_of': at.isoformat(),
            'previous_board_quotes': {**previous.get('quotes', {}), **previous.get('board_quotes', {})}}


def _run(root, now, force_report, collector, evaluator, analyzer, review=False):
    active = 'close' if review else session(now)
    if not active:
        return {'status': 'skipped', 'reason': 'outside_market_window', 'pending_channels': []}
    plan_error = None
    try:
        plan = validate_plan(load(Path(root) / 'plan.json'))
    except Exception as exc:
        plan_error = type(exc).__name__
        plan = {'watchlist': []}
    digest = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()[:10]
    # Provenance is calculated from the original, validated plan before any
    # analysis-side enrichment. It never trusts a free-form plan strategy name.
    # A missing local rule source is recorded as unavailable rather than
    # changing the existing analysis or risk-gate behavior.
    if plan_error:
        strategy = None
        strategy_provenance_error = plan_error
    else:
        try:
            strategy = strategy_identity.strategy_identity(plan)
            strategy_provenance_error = None
        except strategy_identity.StrategyIdentityError as exc:
            strategy = None
            strategy_provenance_error = type(exc).__name__
    slot = now.strftime('%Y%m%d-') + active + '-%02d%02d' % (now.hour, now.minute // 5 * 5)
    if force_report:
        slot += '-' + now.strftime('%S%f')
    report_id = slot + '-' + digest
    path = record_path(root, report_id)
    state_path = folder(root) / 'state.json'
    state = load(state_path, {})
    if path.exists():
        return summary(read_record(root, report_id), now)
    import live_analysis
    error = plan_error
    try:
        if plan_error:
            raise ValueError('plan unavailable')
        snapshot = collector(plan, now)
    except live_analysis.NonTradingDay:
        return {'status': 'skipped', 'reason': 'no_current_trading_data', 'pending_channels': []}
    except Exception as exc:
        error = type(exc).__name__
        snapshot = getattr(exc, 'partial_snapshot', None) or {'as_of': now.isoformat(), 'quotes': {}, 'emotion': {}, 'warnings': []}
    risk_em = snapshot.get('previous_emotion' if now.time() < dt.time(10) else 'emotion', {})
    if 'reentry_required' not in state:
        legacy = load(Path(root) / '.alert_state.json', {})
        legacy_ebb = legacy.get('market:ebb_note') == now.strftime('%Y-%m-%d')
        state = {**state, 'reentry_required': legacy_ebb,
                 'ebb_baseline': {'zt': risk_em.get('zt'),
                                  'zb_rate': risk_em.get('zb_rate')} if legacy_ebb else {}}
    snapshot = {**snapshot, **previous_board_evidence(root, state, now),
                'reentry_required': bool(state.get('reentry_required'))}
    try:
        decision = evaluator(snapshot, plan)
    except Exception as exc:
        error = type(exc).__name__
        decision = failed_decision(plan)
    current_em = snapshot.get('emotion', {})
    was_ebb = any(token in reason for reason in decision['risk_reasons'] for token in ('炸板率', '跌停', '高度板'))
    reentry = bool(state.get('reentry_required')) or was_ebb
    baseline = state.get('ebb_baseline', {})
    if was_ebb and (not state.get('reentry_required') or not baseline):
        baseline = {'zt': risk_em.get('zt'), 'zb_rate': risk_em.get('zb_rate')}
    recovery = (decision.get('reentry_confirmed') and isinstance(baseline.get('zt'), (int, float))
                and isinstance(baseline.get('zb_rate'), (int, float))
                and current_em.get('zt', 0) > baseline['zt']
                and current_em.get('zb_rate', 100) < baseline['zb_rate'])
    if reentry and not was_ebb and not recovery:
        decision = {**decision, 'buy_allowed': False, 'risk_reasons': decision['risk_reasons'] + ['退潮后恢复未确认：需涨停回升、炸板回落、主线连续两日各至少4家'],
                    'stocks': [{**s, 'action': '暂停买入', 'reason': s['reason'] + '；退潮恢复未确认'} if s['action'] == '买入候选' else s for s in decision['stocks']]}
    if recovery:
        reentry = False
    jev = None
    try:
        if not snapshot.get('quotes'):
            raise ValueError('no valid quotes')
        jev = analyzer(snapshot, plan, decision)
    except Exception as exc:
        error = type(exc).__name__
    decision = combine(decision, jev, error)
    try:
        decision = enrich(decision, snapshot, plan)
    except Exception as exc:
        error = type(exc).__name__
        decision = combine(decision, jev, error)
    if active != 'intraday':
        decision = {**decision, 'buy_allowed': False, 'risk_reasons': decision['risk_reasons'] + ['竞价/收盘仅作观察，不产生实时买入候选'],
                    'stocks': [{**s, 'action': '观察', 'reason': s['reason'] + '；非连续交易时段'} if s['action'] == '买入候选' else s for s in decision['stocks']]}
    status = 'error' if error else 'success'
    answers = (jev or {}).get('answers', {})
    fingerprint = hashlib.sha256(json.dumps(['analysis-purchase-validation-v1', now.strftime('%Y-%m-%d'), active, status, decision['buy_allowed'],
        [(s['code'], s['action'], s['jev_action'], s['jev_reason'], s.get('entry_type')) for s in decision['stocks']],
        answers.get('market', {}).get('choice'), answers.get('mainline', {}).get('choice')], ensure_ascii=False).encode()).hexdigest()
    channels = {name: {'status': 'blocked' if name in state.get('unconfirmed', {}) else
        ('pending' if force_report or active == 'heartbeat' or state.get('delivered', {}).get(name) != fingerprint else 'unchanged')} for name in CHANNELS}
    expiry = now + dt.timedelta(minutes=3) if any(s['action'] == '买入候选' for s in decision['stocks']) else max(now + dt.timedelta(minutes=30), now.replace(hour=15, minute=30, second=0))
    coverage = history_coverage(root, now)
    message = make_message(report_id, snapshot, decision, jev, status, active, coverage)
    message_path = path.with_suffix('.md')
    message_path.parent.mkdir(parents=True, exist_ok=True)
    message_path.parent.chmod(0o700)
    message_path.write_text(message, encoding='utf-8')
    message_path.chmod(0o600)
    record = {'report_id': report_id, 'status': status, 'created_at': now.isoformat(), 'expires_at': expiry.isoformat(),
              'snapshot': snapshot, 'decision': decision, 'jev': jev, 'error_type': error, 'fingerprint': fingerprint,
              'backtest_validation': coverage, 'report_format': 'analysis-purchase-validation-v1',
              'strategy': strategy, 'strategy_provenance_error': strategy_provenance_error,
              'channels': channels, 'message': message, 'message_path': str(message_path), 'report_path': str(path)}
    write(path, record)
    write(state_path, {**state, 'latest_id': report_id, 'last_run': now.isoformat(),
                       'reentry_required': reentry, 'ebb_baseline': baseline})
    if active == 'close':
        archive = Path(root) / 'reviews' / (now.strftime('%Y-%m-%d') + '-codex.md')
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.parent.chmod(0o700)
        archive.write_text(message, encoding='utf-8')
        archive.chmod(0o600)
    return {**summary(record, now), 'unconfirmed': state.get('unconfirmed', {})}


def run_cycle(root, now=None, force_report=False, collector=None, evaluator=None, analyzer=None, review=False):
    import live_analysis
    try:
        with lock(root):
            return _run(root, now or now_cn(), force_report, collector or live_analysis.collect_snapshot,
                        evaluator or live_analysis.evaluate, analyzer or live_jev.analyze, review)
    except BlockingIOError:
        return {'status': 'busy', 'pending_channels': []}


def send_feishu(root, report_id, now=None):
    import push
    now = now or now_cn()
    if os.environ.get('NO_PUSH'):
        return {'status': 'dry_run', 'report_id': report_id}
    with lock(root):
        record = read_record(root, report_id)
        push._load_env()
        if not push.ENV.get('LARK_USER_OPEN_ID'):
            return {'status': 'delivery_failed', 'report_id': report_id, 'reason': 'feishu_not_configured'}
        claimed = _claim_locked(root, report_id, 'feishu', now)
        if claimed['status'] != 'ready_to_send':
            return claimed
        result = push.send_feishu_once(record['message'])
        if not result or not result[0]:
            return {'status': 'delivery_uncertain', 'report_id': report_id, 'verify_channel': 'feishu'}
        return _ack_locked(root, report_id, 'feishu', result[1], now)


def send_slack(root, report_id, now=None):
    import slack_bot
    now = now or now_cn()
    if os.environ.get('NO_PUSH'):
        return {'status': 'dry_run', 'report_id': report_id}
    with lock(root):
        state = load(folder(root) / 'state.json', {})
        outstanding = state.get('unconfirmed', {}).get('slack')
        if outstanding:
            return {'status': 'needs_verification', 'report_id': outstanding, 'verify_channel': 'slack'}
        record = read_record(root, report_id)
        if 'slack' not in summary(record, now)['pending_channels']:
            return {'status': 'skipped', 'report_id': report_id}
        # Opening a DM sends no message. Persist its identity before claiming.
        target = slack_bot.open_dm()
        record = {**record, 'slack_transport': 'bot', 'slack_target': target}
        write(record_path(root, report_id), record)
        claimed = _claim_locked(root, report_id, 'slack', now)
        if claimed['status'] != 'ready_to_send':
            return claimed
        result = slack_bot.send_once(record['message'], channel=target)
        if not result.get('ok'):
            return {'status': 'delivery_uncertain', 'report_id': report_id, 'verify_channel': 'slack'}
        return _ack_locked(root, report_id, 'slack', result['ts'], now)


def verify_slack(root, report_id, now=None):
    import slack_bot
    now = now or now_cn()
    with lock(root):
        record = read_record(root, report_id)
        if record.get('slack_transport') != 'bot':
            return {'status': 'legacy_connector_verification_required', 'report_id': report_id}
        started = record['channels']['slack'].get('started_at', record['created_at'])
        oldest = dt.datetime.fromisoformat(started).replace(tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp() - 60
        result = slack_bot.verify(report_id, record['slack_target'], str(oldest))
        if result['status'] == 'verified':
            return _ack_locked(root, report_id, 'slack', result['receipt'], now)
        # Keep uncertainty until the caller reviews negative delivery evidence.
        return {'status': 'verified_absent' if result['status'] == 'absent' else 'needs_verification',
                'report_id': report_id, 'verify_channel': 'slack'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('pending')
    run = commands.add_parser('run')
    run.add_argument('--force-report', action='store_true')
    run.add_argument('--review', action='store_true', help='manual post-market analysis, never a buy signal')
    for name in ('ack', 'send-feishu', 'send-slack', 'verify-slack', 'claim', 'release'):
        child = commands.add_parser(name)
        child.add_argument('--id', required=True)
        if name in ('ack', 'claim', 'release'):
            child.add_argument('--channel', choices=CHANNELS, required=True)
        if name in ('ack', 'release'):
            child.add_argument('--receipt', required=True)
    args = parser.parse_args()
    root = Path(datadir.data_dir())
    try:
        if args.command == 'run':
            result = run_cycle(root, force_report=args.force_report, review=args.review)
        elif args.command == 'pending':
            result = pending(root)
        elif args.command == 'ack':
            result = ack(root, args.id, args.channel, args.receipt)
        elif args.command == 'claim':
            result = claim(root, args.id, args.channel)
        elif args.command == 'release':
            result = release(root, args.id, args.channel, args.receipt)
        elif args.command == 'send-slack':
            result = send_slack(root, args.id)
        elif args.command == 'verify-slack':
            result = verify_slack(root, args.id)
        else:
            result = send_feishu(root, args.id)
        print(json.dumps(result, ensure_ascii=False))
        return 1 if isinstance(result, dict) and result.get('status') in ('delivery_failed', 'delivery_uncertain') else 0
    except Exception as exc:
        print(json.dumps({'status': 'error', 'error_type': type(exc).__name__, 'message': '本轮失败，禁止新买入；检查本地配置或状态文件，勿重置送达记录。'}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
