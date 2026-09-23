import datetime as dt
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'stock-signal/scripts'))
import codex_cycle as cycle
from report_sections import history_coverage, purchase_section


def test_pause_never_displays_purchase_budget():
    decision = {'buy_allowed': False, 'stocks': [{'code': 'sz000001', 'name': '样例', 'action': '买入候选', 'budget': 5000, 'price': 10}]}
    text = '\n'.join(purchase_section(decision, '2026-09-23T10:00:00', 'success', 'intraday'))
    assert '本轮不建议新买入' in text
    assert '5000' not in text


def test_purchase_has_expiry_invalidation_and_no_research_allocation():
    decision = {'buy_allowed': True, 'stocks': [
        {'code': 'sz000001', 'name': '正式', 'action': '买入候选', 'price': 10, 'budget': 5000, 'size_pct': 5, 'shares': 500, 'buy_reference': '核对回封'},
        {'code': 'sz000002', 'name': '研究', 'action': '买入候选', 'research_only': True, 'budget': 9999}]}
    text = '\n'.join(purchase_section(decision, '2026-09-23T10:00:00', 'success', 'intraday'))
    assert '正式' in text and '5000' in text
    assert '研究' not in text and '9999' not in text
    assert '10:03:00' in text and '失效' in text
    assert '正式' not in '\n'.join(purchase_section(decision, '2026-09-23T10:00:00', 'error', 'intraday'))
    assert '正式' not in '\n'.join(purchase_section(decision, '2026-09-23T10:00:00', 'success', 'close'))


def test_coverage_is_not_fabricated_backtest_and_ignores_future(tmp_path):
    reports = tmp_path / 'codex_monitor/reports'
    reports.mkdir(parents=True)
    record = {'created_at': '2026-09-22T10:00:00', 'status': 'success', 'decision': {'buy_allowed': True, 'stocks': [{'code': 'sz000001', 'action': '买入候选'}, {'code': 'sz000002', 'action': '买入候选', 'research_only': True}]}}
    (reports / 'a.json').write_text(json.dumps(record))
    (reports / 'future.json').write_text(json.dumps({**record, 'created_at': '2026-09-24T10:00:00'}))
    (reports / 'broken.json').write_text('{bad')
    result = history_coverage(tmp_path, dt.datetime(2026, 9, 23, 10))
    assert result['status'] == 'not_backtested'
    assert result['records'] == 1 and result['candidate_observations'] == 1
    assert result['unreadable'] == 1
    assert 'win_rate' not in result and 'returns' not in result
