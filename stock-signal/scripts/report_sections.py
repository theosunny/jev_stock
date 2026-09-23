"""Separate actionable reminders from research and unvalidated performance claims."""
import datetime as dt
import json
from pathlib import Path


def purchase_section(decision, as_of, status, session):
    lines = ['\n**二、买入建议（条件提醒，不代下单）**']
    candidates = [s for s in decision['stocks']
                  if s['action'] == '买入候选' and not s.get('research_only')
                  and s.get('budget', 0) > 0 and s.get('shares', 0) > 0]
    if status != 'success' or session != 'intraday' or not decision['buy_allowed']:
        candidates = []
    if not candidates:
        return lines + ['本轮不建议新买入：无通过全部条件的可执行候选。观察名单不等于购买名单。']
    expiry = dt.datetime.fromisoformat(as_of) + dt.timedelta(minutes=3)
    for s in candidates:
        lines += ['- %s %s｜%s｜快照价%s元' % (s['name'], s['code'], s.get('entry_type') or '条件买入', s.get('price', '未知')),
                  '  触发依据：' + s.get('reason', '见分析'),
                  '  参与条件：' + s.get('buy_reference', '核对最新价格与计划条件'),
                  '  计划上限参考：%s%% / %s元 / %s股；核实实际剩余仓位，不是必须买足。' % (s.get('size_pct', '?'), s['budget'], s['shares']),
                  '  失效：超过%s、价格离开条件、封板打开或主线/风控转弱，立即作废，等待重新确认。' % expiry.isoformat(),
                  '  排队不保证成交；不自动委托或撤单。']
    return lines


def history_coverage(root, now):
    """Audit only the last 30 calendar days of archives; never infer fills/returns."""
    records = errors = unreadable = observations = 0
    days, symbols = set(), set()
    for path in (Path(root) / 'codex_monitor' / 'reports').glob('*.json'):
        try:
            record = json.loads(path.read_text(encoding='utf-8'))
            created = dt.datetime.fromisoformat(record['created_at'])
            if not dt.timedelta(0) <= now - created <= dt.timedelta(days=30):
                continue
            decision = record['decision']
            stocks = decision['stocks']
            selected = [s['code'] for s in stocks if s['action'] == '买入候选'
                        and not s.get('research_only') and decision.get('buy_allowed')
                        and record['status'] == 'success']
            records += 1
            errors += record['status'] != 'success'
            days.add(created.date().isoformat())
            observations += len(selected)
            symbols.update(selected)
        except (OSError, ValueError, KeyError, TypeError, AttributeError):
            unreadable += 1
    return {'status': 'not_backtested', 'window_days': 30, 'records': records,
            'trading_dates': len(days), 'error_records': errors, 'unreadable': unreadable,
            'candidate_observations': observations, 'candidate_symbols': len(symbols),
            'as_of': now.isoformat()}


def backtest_section(coverage=None):
    lines = ['\n**三、回测验证**', '状态：尚未完成收益回测；不能据此声称胜率、收益率或策略有效。']
    if coverage:
        lines.append('近30日归档核查（不含当前轮）：%s个日期 / %s轮，失败%s轮；买入候选出现%s次、涉及%s只股票（重复轮次不等于独立交易）。' % (
            coverage['trading_dates'], coverage['records'], coverage['error_records'],
            coverage['candidate_observations'], coverage['candidate_symbols']))
        if coverage['unreadable']:
            lines.append('有%s份归档无法读取，统计不完整；原文件保留，需核查。' % coverage['unreadable'])
    lines.append('验证缺口：逐时历史计划/策略版本、无未来信息的历史样本，以及含排队未成交、费用、滑点、T+1的成交模型。触及涨停价不能假定买到。')
    return lines
