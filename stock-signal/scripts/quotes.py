# -*- coding: utf-8 -*-
"""免费行情数据（多源: 腾讯→东财，无需key）。实时: qt.gtimg.cn/push2.eastmoney.com  日线: web.ifzq.gtimg.cn"""
import json, re, urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}
_EM_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
          "Referer": "https://quote.eastmoney.com/"}

def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def _get_json_em(url, timeout=10):
    req = urllib.request.Request(url, headers=_EM_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

def _fetch_tencent(codes):
    """Fetch from Tencent qt.gtimg.cn (primary source)"""
    raw = _get("https://qt.gtimg.cn/q=" + ",".join(codes)).decode("gbk", "ignore")
    out = {}
    for m in re.finditer(r'v_(\w+)="([^"]*)"', raw):
        code, f = m.group(1), m.group(2).split("~")
        if len(f) < 49 or not f[3]:
            continue
        out[code] = {
            "name": f[1],
            "price": float(f[3]),
            "prev_close": float(f[4]),
            "open": float(f[5]),
            "high": float(f[33] or f[3]),
            "low": float(f[34] or f[3]),
            "pct": float(f[32] or 0),
            "limit_up": float(f[47]) if f[47] else None,
            "limit_down": float(f[48]) if f[48] else None,
            "vwap": (round(float(f[37]) * 100 / float(f[36]), 3)
                     if f[36] and float(f[36]) > 0 and f[37] else None),
            "volume": float(f[36]) if f[36] else 0.0,
            "bid1_price": float(f[9]) if f[9] else None,
            "bid1_volume": float(f[10]) if f[10] else None,
            "time": f[30],
            "source": "tencent",
        }
    return out


def _fetch_eastmoney(codes):
    """Fetch from East Money push2/push2delay (fallback source)"""
    out = {}
    for code in codes:
        try:
            market_id = "1" if code.startswith("sh") else "0"
            stock_code = code[2:]
            secid = f"{market_id}.{stock_code}"
            
            fields = "f57,f58,f43,f44,f60,f45,f46,f47,f48,f19,f161,f162,f163,f164,f165,f152,f84,f85,f86,f168,f169"
            for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
                try:
                    url = f"{host}/api/qt/stock/get?secid={secid}&invt=2&fltt=2&fields={fields}"
                    data = _get_json_em(url)
                    d = data.get("data")
                    if not d or not isinstance(d, dict):
                        continue
                    
                    price = d.get("f43")
                    prev_close = d.get("f60")
                    open_price = d.get("f46")
                    high = d.get("f44")
                    low = d.get("f45")
                    pct = d.get("f170") if d.get("f170") is not None else (
                        round((price / prev_close - 1) * 100, 2) if price and prev_close and prev_close > 0 else 0)
                    volume = d.get("f47")
                    amount = d.get("f48")
                    name = d.get("f58", code)
                    
                    if price is None or prev_close is None:
                        continue
                    
                    out[code] = {
                        "name": name,
                        "price": float(price) / 100 if isinstance(price, (int, float)) else 0.0,
                        "prev_close": float(prev_close) / 100 if isinstance(prev_close, (int, float)) else 0.0,
                        "open": float(open_price) / 100 if open_price is not None else 0.0,
                        "high": float(high) / 100 if high is not None else 0.0,
                        "low": float(low) / 100 if low is not None else 0.0,
                        "pct": float(pct) if pct is not None else 0.0,
                        "limit_up": None,
                        "limit_down": None,
                        "vwap": (round(amount / volume, 3) if volume and amount and volume > 0 else None),
                        "volume": float(volume) if volume else 0.0,
                        "bid1_price": None,
                        "bid1_volume": None,
                        "time": d.get("f86", ""),
                        "source": "eastmoney",
                    }
                    break
                except Exception:
                    continue
        except Exception:
            continue
    return out


def fetch_quotes(codes):
    """codes如['sz002185'] -> {code: {name,price,prev_close,open,high,low,pct,limit_up,limit_down,time}}
    
    Multi-source with fallback: Tencent (primary) → East Money (fallback)
    Returns unified quote dict with 'source' field tracking which vendor supplied data.
    """
    result = {}
    missing = []
    
    # Try Tencent first
    try:
        tencent_quotes = _fetch_tencent(codes)
        for code in codes:
            if code in tencent_quotes:
                result[code] = tencent_quotes[code]
            else:
                missing.append(code)
    except Exception:
        missing = list(codes)
    
    # Fallback to East Money for missing codes
    if missing:
        try:
            em_quotes = _fetch_eastmoney(missing)
            result.update(em_quotes)
        except Exception:
            pass
    
    return result

def fetch_ma5(code):
    """近5个交易日收盘均价（含当日）"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=" + code + ",day,,,8,qfq"
    data = json.loads(_get(url).decode("utf-8", "ignore"))
    d = data.get("data", {}).get(code, {})
    rows = d.get("qfqday") or d.get("day") or []
    closes = [float(r[2]) for r in rows if r[2]]
    if len(closes) < 2:
        return None
    tail = closes[-5:]
    return sum(tail) / len(tail)


def fetch_prev_volume(code):
    """上一交易日成交量(股)，用于放量判断"""
    url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=" + code + ",day,,,8,qfq"
    data = json.loads(_get(url).decode("utf-8", "ignore"))
    d = data.get("data", {}).get(code, {})
    rows = d.get("qfqday") or d.get("day") or []
    if len(rows) >= 2:
        return float(rows[-2][5])
    return None
