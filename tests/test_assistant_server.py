import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from assistant.server import make_server


def request(base, path, method='GET', body=None, headers=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(base + path, data=data, method=method,
        headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req) as response:
            return response.status, response.read(), dict(response.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def test_local_http_boundaries(tmp_path):
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        status, body, headers = request(base, '/')
        assert status == 200 and b'JEV' in body
        assert "default-src 'self'" in headers['Content-Security-Policy']
        assert request(base, '/.env')[0] == 404
        assert request(base, '/api/bootstrap', headers={'Host': 'evil.example'})[0] == 403
        assert request(base, '/api/bootstrap', headers={'Origin': 'https://evil.example'})[0] == 403
        assert request(base, '/api/simulation/orders', 'POST', {'id':'x'})[0] == 403
        status, body, _ = request(base, '/api/bootstrap')
        token = json.loads(body)['data']['token']
        assert token
        assert request(base, '/api/simulation/orders', 'POST', {'id':'x'},
            {'X-Session-Token': token, 'Origin': 'https://evil.example'})[0] == 403
        assert request(base, '/api/stock?code=../../.env')[0] == 400
        assert request(base, '/api/broker/orders', 'POST', {}, {'X-Session-Token':token})[0] == 403
        assert request(base, '/api/overview')[0] == 200
        assert request(base, '/api/validation')[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_simulation_uses_nested_provider_quote_and_current_gate(tmp_path, monkeypatch):
    from assistant.server import Application
    from assistant import market_data
    app = Application(tmp_path)
    class FakeLedger:
        def __init__(self):
            self.cancelled = []
        def summary(self):
            return {'orders': [o for o in [{'id':'a', 'recommendation_id':'current','code':'sz002185', 'status':'pending'},
                               {'id':'b', 'recommendation_id':'old','code':'sz002185', 'status':'pending'}] if o['id'] not in self.cancelled]}
        def cancel_order(self, rid):
            self.cancelled.append(rid)
        def process_quotes(self, quotes, now):
            assert quotes[0]['price'] == 10
            assert quotes[0]['time'] == '2026-09-23T14:01:00'
            assert len(quotes) == 1
            return {'filled': 0, 'pending': 1}
        def close_position(self, code, quote, now):
            assert quote['code'] == code and quote['price'] == 10
            return {'status': 'unfilled'}
    app.ledger.close()
    app.ledger = FakeLedger()
    monkeypatch.setattr(app, 'overview', lambda: {'candidates':[{'code':'sz002185','formal_candidate':True}], 'current_recommendation_ids':['current']})
    monkeypatch.setattr(market_data, 'get_quote', lambda code: {'code':code, 'quote':{'code':code,'price':10,'time':'20260923140100'},'warnings':[]})
    assert app.refresh_simulation()['risk_cancelled'] == 1
    assert app.ledger.cancelled == ['b']
    assert app.close_simulation({'code':'sz002185'})['status'] == 'unfilled'


def test_guojin_always_disabled():
    import pytest
    from assistant.broker import GuojinDisabled
    broker = GuojinDisabled()
    assert broker.status()['live_enabled'] is False
    with pytest.raises(PermissionError):
        broker.submit_order({'code':'sz002185'})
    with pytest.raises(PermissionError):
        broker.cancel_order('x')


def test_real_archive_to_application_preserves_historical_but_only_allows_current(tmp_path, monkeypatch):
    import datetime as dt
    import pytest
    from assistant.server import Application
    from assistant import data
    now = dt.datetime(2026, 9, 23, 10, 2)
    stock = {'code':'sz000001','name':'测试样本','price':10,'action':'买入候选','budget':5000,
             'research_only':False,'buy_enabled':True,'entry_condition':'站稳VWAP','reason':'测试'}
    reports = tmp_path / 'codex_monitor/reports'
    reviews = tmp_path / 'reviews'
    reports.mkdir(parents=True); reviews.mkdir()
    for rid,time in [('old','10:00:00'),('current','10:01:00')]:
        (reports / f'{rid}.json').write_text(json.dumps({'report_id':rid,'created_at':f'2026-09-23T{time}',
            'expires_at':'2026-09-23T10:04:00','status':'success',
            'decision':{'buy_allowed':True,'stocks':[stock]}}))
    review = {'report_id':'rolling','as_of':'2026-09-23T10:01:30','jev_status':'success',
              'decision':{'buy_allowed':True}, 'candidates':[stock]}
    (reviews / 'latest-rolling.json').write_text(json.dumps(review))
    actual_load = data.load_overview
    monkeypatch.setattr(data,'load_overview',lambda path:actual_load(path,now=now))
    app = Application(tmp_path)
    actual_order=app.ledger.create_order
    monkeypatch.setattr(app.ledger,'create_order',lambda rid:actual_order(rid,now=now))
    view=app.overview()
    assert view['current_recommendation_ids']==['current:sz000001']
    assert len(app.ledger.summary(now=now)['recommendations'])==2
    with pytest.raises(ValueError):
        app.simulate({'id':'old:sz000001'})
    assert app.simulate({'id':'current:sz000001'})['status']=='pending'
    app.ledger.close()


def test_risk_rechecked_after_quote_fetch_before_paper_fill(tmp_path, monkeypatch):
    from assistant.server import Application
    from assistant import market_data
    app = Application(tmp_path)
    class Pending:
        def __init__(self):
            self.cancelled = False
        def summary(self):
            return {'orders': [] if self.cancelled else [{'id':'p','recommendation_id':'r','code':'sz002185','status':'pending'}]}
        def cancel_order(self, order_id):
            self.cancelled = True
        def process_quotes(self, quotes, now):
            assert self.cancelled, 'risk must be checked again after provider fetch'
            return {'filled':0,'pending':0}
    app.ledger.close(); app.ledger = Pending()
    views=iter([{'current_recommendation_ids':['r']},{'current_recommendation_ids':[]}])
    monkeypatch.setattr(app,'overview',lambda:next(views))
    monkeypatch.setattr(market_data,'get_quote',lambda code:{'quote':{'code':code,'price':10,'time':'20260923100200'}})
    assert app.refresh_simulation()=={'filled':0,'pending':0,'risk_cancelled':1}


def test_strategy_catalog_is_read_only(tmp_path):
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        status, body, _ = request(base, '/api/strategies')
        assert status == 200
        catalog = json.loads(body)['data']
        assert catalog['current_strategy_id'] in [s['id'] for s in catalog['strategies']]
        token = json.loads(request(base, '/api/bootstrap')[1])['data']['token']
        assert request(base, '/api/strategies', 'POST', {'id':'buffett'}, {'X-Session-Token':token})[0] == 404
        assert not (tmp_path / 'plan.json').exists()
    finally:
        server.shutdown()
        server.server_close()


def test_comparison_requires_known_archive_ids(tmp_path):
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        assert request(base, '/api/strategy-comparison?before=../plan.json&after=x')[0] == 400
        assert request(base, '/api/strategy-comparison')[0] == 400
    finally:
        server.shutdown()
        server.server_close()


def test_strategy_comparison_returns_actual_membership_change(tmp_path):
    reviews = tmp_path / 'reviews'
    reviews.mkdir()
    for i, codes in enumerate([['sz000001'], ['sz000001', 'sz000002']], 1):
        value = {'report_id':f'r{i}', 'as_of':f'2026-09-23T10:0{i}:00', 'jev_status':'success',
                 'strategy':{'id':'test-strategy','name':'测试策略','version':str(i)},
                 'candidates':[{'code':code,'name':code,'status':'保留','reason':'归档理由'} for code in codes]}
        (reviews / f'{i}-rolling.json').write_text(json.dumps(value))
    server = make_server(tmp_path, port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    try:
        status, body, _ = request(base, '/api/strategy-comparison?before=r1&after=r2')
        result = json.loads(body)['data']
        assert status == 200 and result['available']
        assert result['kind'] == 'strategy_change'
        assert [r['code'] for r in result['added']] == ['sz000002']
        assert result['removed'] == []
        _, body, _ = request(base, '/api/strategy-comparison?before=r2&after=r1')
        assert json.loads(body)['data']['available'] is False
    finally:
        server.shutdown()
        server.server_close()
