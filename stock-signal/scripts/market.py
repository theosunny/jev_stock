# -*- coding: utf-8 -*-
"""市场情绪与板块数据（东方财富公开接口，免key）
- emotion(): 涨停/跌停/炸板池 -> 涨停家数、最高连板、炸板率、连板梯队
- sectors(): 行业/概念板块涨幅榜（含领涨股）
- stock_industry(code): 个股所属行业+概念
"""
import datetime as dt
import json
import urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
       "Referer": "https://quote.eastmoney.com/"}
_UT = "7eea3edcaed734bea9cbfc24409ed989"

def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

def _pool(kind, date):
    url = ("https://push2ex.eastmoney.com/getTopic%sPool?ut=%s&dpt=wz.ztzt"
           "&Pageindex=0&pagesize=320&sort=fbt%%3Aasc&date=%s" % (kind, _UT, date))
    try:
        d = _get_json(url)
        return (d.get("data") or {}).get("pool") or []
    except Exception:
        return []

def _latest_trading_date(max_back=7):
    for i in range(max_back):
        d = (dt.date.today() - dt.timedelta(days=i)).strftime("%Y%m%d")
        if _pool("ZT", d):
            return d
    return None

def emotion(date=None):
    """date: YYYYMMDD 或 None(自动最近交易日)。返回情绪概览 dict"""
    date = date or _latest_trading_date()
    if not date:
        return {"error": "no_data"}
    zt, zb, dtp = _pool("ZT", date), _pool("ZB", date), _pool("DT", date)
    lbs = [int(p.get("lbc", 1)) for p in zt]
    ladder = {}
    for p in zt:
        lb = int(p.get("lbc", 1))
        if lb >= 2:
            ladder.setdefault(lb, []).append(p.get("n", ""))
    zb_rate = round(len(zb) * 100.0 / (len(zt) + len(zb)), 1) if (zt or zb) else 0.0
    return {"date": date, "zt": len(zt), "zb": len(zb), "dt": len(dtp),
            "zb_rate": zb_rate, "max_lb": max(lbs) if lbs else 0, "ladder": ladder}

def emotion_line(date=None):
    """一行式情绪摘要，直接用于推送"""
    em = emotion(date)
    if "error" in em:
        return ""
    lad = " ".join("%d板x%d" % (lb, len(ns)) for lb, ns in sorted(em["ladder"].items(), reverse=True)[:4])
    s = "情绪: 涨停%d 炸板%d(率%s%%) 跌停%d 最高%d板" % (
        em["zt"], em["zb"], em["zb_rate"], em["dt"], em["max_lb"])
    if lad:
        s += " | " + lad
    return s

def sectors(top=10, kind=2):
    """kind: 2=行业板块 3=概念板块。返回 [{name, pct}]；主域名限流时自动切 push2delay"""
    import urllib.error
    path = ("/api/qt/clist/get?pn=1&pz=%d&po=1&np=1"
            "&fltt=2&invt=2&fid=f3&fs=m:90+t:%d+f:!50&fields=f3,f12,f14" % (top, kind))
    for host in ("https://push2.eastmoney.com", "https://push2delay.eastmoney.com"):
        try:
            rows = ((_get_json(host + path).get("data") or {}).get("diff")) or []
            if rows:
                return [{"name": r.get("f14"), "pct": r.get("f3")}
                        for r in rows if isinstance(r.get("f3"), (int, float))]
        except Exception:
            continue
    return []

def sector_heat(date=None, top=5):
    """涨停池聚合: 板块涨停家数分布 -> [(板块, 家数)]，主线判定核心（不依赖clist）"""
    from collections import Counter
    date = date or _latest_trading_date()
    if not date:
        return []
    heat = Counter(p.get("hybk", "其他") for p in _pool("ZT", date))
    return heat.most_common(top)

def heat_line(date=None, top=5):
    rows = sector_heat(date, top)
    return "涨停分布: " + " ".join("%s%d家" % (n, c) for n, c in rows) if rows else ""

def sector_line(top=3, kind=2, label="行业"):
    rows = sectors(top, kind)
    if not rows:
        return ""
    return label + ": " + " ".join("%s%+.1f%%" % (r["name"], r["pct"]) for r in rows)

def stock_industry(code):
    """code: sz002185 -> {'industry': 行业名}（尽力而为，失败返回空dict）"""
    m = "1" if code.startswith("sh") else "0"
    url = ("https://push2delay.eastmoney.com/api/qt/stock/get?secid=%s.%s&invt=2&fltt=2&fields=f127"
           % (m, code[2:]))
    try:
        d = _get_json(url).get("data") or {}
        return {"industry": d.get("f127")}
    except Exception:
        return {}
