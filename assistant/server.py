"""Loopback-only personal research UI, separate from the live monitoring chain."""
import argparse
from datetime import datetime
from zoneinfo import ZoneInfo
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import mimetypes
import os
from pathlib import Path
import re
import secrets
import threading
from urllib.parse import parse_qs, urlsplit

STATIC = Path(__file__).parent / 'static'
CODE = re.compile(r'^(sh|sz)\d{6}$')


class Application:
    def __init__(self, data_dir):
        from .ledger import Ledger
        self.data_dir = Path(data_dir)
        self.ledger = Ledger(self.data_dir / 'assistant' / 'ledger.sqlite3')
        self.token = secrets.token_urlsafe(32)
        self.lock = threading.RLock()

    def overview(self):
        from .data import load_overview
        from .broker import GuojinDisabled
        data = load_overview(self.data_dir)
        data['broker'] = GuojinDisabled().status()
        with self.lock:
            self.ledger.ingest(data.get('recommendations', []))
        return data

    def validation(self):
        self.overview()
        with self.lock:
            return self.ledger.summary()

    def simulate(self, payload):
        overview = self.overview()
        rid = payload.get('id')
        if not isinstance(rid, str) or not 1 <= len(rid) <= 240:
            raise ValueError('请选择有效的正式推荐记录')
        recommendation = next((r for r in overview.get('recommendations', []) if r['id'] == rid), None)
        if not recommendation or rid not in overview.get('current_recommendation_ids', []):
            raise ValueError('当前分析未通过全部条件，仅保留推荐跟踪')
        with self.lock:
            return self.ledger.create_order(rid)


    def refresh_simulation(self):
        from .market_data import get_quote
        overview = self.overview()
        allowed_ids = set(overview.get('current_recommendation_ids', []))
        cancelled = 0
        with self.lock:
            pending = [o for o in self.ledger.summary()['orders'] if o['status'] == 'pending']
            for order in pending:
                if order['recommendation_id'] not in allowed_ids:
                    self.ledger.cancel_order(order['id'])
                    cancelled += 1
            codes = {o['code'] for o in pending if o['recommendation_id'] in allowed_ids}
        quotes = []
        for code in sorted(codes):
            if CODE.fullmatch(code):
                quote = dict(get_quote(code).get('quote') or {})
                raw = quote.get('time', '')
                if isinstance(raw, str) and re.fullmatch(r'\d{14}', raw):
                    quote['time'] = datetime.strptime(raw, '%Y%m%d%H%M%S').isoformat()
                quotes.append(quote)
        final_ids = set(self.overview().get('current_recommendation_ids', []))
        with self.lock:
            for order in self.ledger.summary()['orders']:
                if order['status'] == 'pending' and order['recommendation_id'] not in final_ids:
                    self.ledger.cancel_order(order['id'])
                    cancelled += 1
            result = self.ledger.process_quotes(quotes, now=datetime.now(ZoneInfo('Asia/Shanghai')))
            return {**result, 'risk_cancelled': cancelled}

    def close_simulation(self, payload):
        from .market_data import get_quote
        code = payload.get('code', '')
        if not isinstance(code, str) or not CODE.fullmatch(code):
            raise ValueError('股票代码格式不正确')
        quote = dict(get_quote(code).get('quote') or {})
        raw = quote.get('time', '')
        if isinstance(raw, str) and re.fullmatch(r'\d{14}', raw):
            quote['time'] = datetime.strptime(raw, '%Y%m%d%H%M%S').isoformat()
        with self.lock:
            return self.ledger.close_position(code, quote, now=datetime.now(ZoneInfo('Asia/Shanghai')))

    def cancel_simulation(self, payload):
        identifier = payload.get('id')
        if not isinstance(identifier, str) or len(identifier) > 100:
            raise ValueError('请选择模拟委托')
        with self.lock:
            return self.ledger.cancel_order(identifier)


class Handler(BaseHTTPRequestHandler):
    server_version = 'JEVLocal/1'

    def log_message(self, fmt, *args):
        # Avoid printing user input, tokens or sensitive provider messages.
        pass

    def send(self, status, body, content_type='application/json; charset=utf-8'):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode()
        self.send_response(status)
        self.send_header('Content-Type', content_type)
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        self.wfile.write(body)

    def result(self, value):
        self.send(200, {'success': True, 'data': value, 'error': None})

    def error(self, status, message):
        self.send(status, {'success': False, 'data': None, 'error': message})

    def allowed(self, mutation=False):
        authority = f'127.0.0.1:{self.server.server_port}'
        if self.headers.get('Host') != authority:
            self.error(403, '仅允许本机地址访问')
            return False
        origin = self.headers.get('Origin')
        if origin and origin != f'http://{authority}':
            self.error(403, '来源校验未通过')
            return False
        if self.headers.get('Sec-Fetch-Site') == 'cross-site':
            self.error(403, '不允许跨站请求')
            return False
        if mutation and not secrets.compare_digest(self.headers.get('X-Session-Token', ''), self.server.app.token):
            self.error(403, '页面会话已失效，请刷新')
            return False
        return True

    def do_GET(self):
        if not self.allowed():
            return
        url = urlsplit(self.path)
        try:
            if url.path == '/api/bootstrap':
                return self.result({'token': self.server.app.token})
            if url.path == '/api/strategy-comparison':
                from .data import load_overview
                from .provenance import compare_snapshots
                params = parse_qs(url.query)
                before, after = params.get('before', [''])[0], params.get('after', [''])[0]
                if not before or not after or max(len(before), len(after)) > 240:
                    return self.error(400, '请选择两份归档')
                history = load_overview(self.server.app.data_dir).get('selection_history', [])
                first = next((r for r in history if r['report_id'] == before), None)
                last = next((r for r in history if r['report_id'] == after), None)
                if not first or not last:
                    return self.error(400, '归档不在当前可比较范围内，请刷新列表')
                return self.result(compare_snapshots(first, last))
            if url.path == '/api/strategies':
                from .strategies import get_catalog
                return self.result(get_catalog())
            if url.path == '/api/overview':
                return self.result(self.server.app.overview())
            if url.path == '/api/validation':
                return self.result(self.server.app.validation())
            if url.path == '/api/market':
                from .market_data import get_market
                return self.result(get_market())
            if url.path == '/api/stock':
                code = parse_qs(url.query).get('code', [''])[0]
                if not CODE.fullmatch(code):
                    return self.error(400, '股票代码格式不正确')
                from .market_data import get_stock
                return self.result(get_stock(code))
            assets = {'/': 'index.html', '/index.html': 'index.html', '/styles.css': 'styles.css', '/app.js': 'app.js', '/ui.js': 'ui.js', '/pages.js': 'pages.js'}
            name = assets.get(url.path)
            if not name or not (STATIC / name).is_file():
                return self.error(404, '未找到页面')
            return self.send(200, (STATIC / name).read_bytes(), mimetypes.guess_type(name)[0] or 'text/plain')
        except (ValueError, OSError, json.JSONDecodeError):
            self.error(503, '数据读取失败，原文件已保留，请检查数据目录或账本')
        except Exception:
            self.error(503, '服务暂不可用，未执行交易，请检查本地服务')

    def do_POST(self):
        if not self.allowed(mutation=True):
            return
        if self.path.startswith('/api/broker'):
            return self.error(403, '国金证券尚未连接，实盘交易关闭')
        try:
            size = int(self.headers.get('Content-Length', '0'))
            if not 0 < size <= 4096:
                return self.error(400, '请求大小不正确')
            payload = json.loads(self.rfile.read(size))
            if not isinstance(payload, dict):
                return self.error(400, '请求格式不正确')
            if self.path == '/api/simulation/refresh':
                return self.result(self.server.app.refresh_simulation())
            if self.path == '/api/simulation/close':
                return self.result(self.server.app.close_simulation(payload))
            if self.path == '/api/simulation/cancel':
                return self.result(self.server.app.cancel_simulation(payload))
            if self.path == '/api/simulation/orders':
                return self.result(self.server.app.simulate(payload))
            self.error(404, '未找到操作')
        except ValueError as exc:
            self.error(400, str(exc)[:200])
        except Exception:
            self.error(503, '操作未完成，请保留账本并检查；不会执行实盘交易')


def make_server(data_dir, port=8766):
    server = ThreadingHTTPServer(('127.0.0.1', port), Handler)
    server.app = Application(data_dir)
    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--data-dir', default=os.environ.get('STOCK_DATA_DIR'))
    args = parser.parse_args()
    if not args.data_dir:
        parser.error('请设置 STOCK_DATA_DIR 或 --data-dir')
    server = make_server(args.data_dir, args.port)
    print(f'JEV 个人交易助手 http://127.0.0.1:{server.server_port}（实盘关闭）', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
