# -*- coding: utf-8 -*-
"""免费行情数据（腾讯接口，无需key）。实时: qt.gtimg.cn  日线: web.ifzq.gtimg.cn"""
import json, re, urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)"}

def _get(url, timeout=10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def fetch_quotes(codes):
    """codes如['sz002185'] -> {code: {name,price,prev_close,open,high,low,pct,limit_up,limit_down,time}}"""
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
            # Tencent fields 9/10 are the current best buy price/quantity.
            # This is a point-in-time order-book observation only: it cannot
            # prove a user's queue position, later cancellations, or fills.
            "bid1_price": float(f[9]) if f[9] else None,
            "bid1_volume": float(f[10]) if f[10] else None,
            "time": f[30],
        }
    return out

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
