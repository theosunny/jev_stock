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

def prev_trading_date(before, max_back=10):
    """早于 before(YYYYMMDD) 的最近有涨停数据的交易日"""
    base = dt.datetime.strptime(before, "%Y%m%d").date()
    for i in range(1, max_back + 1):
        d = (base - dt.timedelta(days=i)).strftime("%Y%m%d")
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

def emotion_line(date=None, label=""):
    """一行式情绪摘要，label如"昨日"用于开盘首小时"""
    em = emotion(date)
    if "error" in em:
        return ""
    lad = " ".join("%d板x%d" % (lb, len(ns)) for lb, ns in sorted(em["ladder"].items(), reverse=True)[:4])
    s = label + "情绪: 涨停%d 炸板%d(率%s%%) 跌停%d 最高%d板" % (
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

def heat_line(date=None, top=5, label=""):
    rows = sector_heat(date, top)
    return label + "涨停分布: " + " ".join("%s%d家" % (n, c) for n, c in rows) if rows else ""

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


def sector_zt_count(industry, date=None):
    """某行业当日涨停家数（主线联动过滤用）。失败返回None"""
    if not industry:
        return None
    date = date or _latest_trading_date()
    if not date:
        return None
    try:
        return sum(1 for p in _pool("ZT", date) if p.get("hybk") == industry)
    except Exception:
        return None


_RISK_KW = ("减持", "立案", "调查", "预亏", "亏损", "停牌", "退市", "质押",
            "违规", "警示", "监管", "处罚", "诉讼", "仲裁", "终止", "问询")
_GOOD_KW = ("增持", "回购", "中标", "预增", "扭亏", "签订", "收购", "战略合作")

def _get_gbk(url, timeout=10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("gbk", "ignore")

def announcements(code):
    """个股近1日公告扫描（东财接口）。code: 002185。返回 {risk:[], good:[]}"""
    sym = code[2:] if code[:2] in ("sz", "sh") else code
    url = ("https://np-anotice-stock.eastmoney.com/api/security/ann?sr=-1&page_size=6"
           "&page_index=1&ann_type=A&client_source=web&stock_list=" + sym)
    out = {"risk": [], "good": []}
    try:
        rows = (_get_json(url).get("data") or {}).get("list") or []
    except Exception:
        return out
    import datetime as _dt
    cutoff = (_dt.date.today() - _dt.timedelta(days=1)).strftime("%Y-%m-%d")
    for r in rows[:6]:
        t, when = (r.get("title") or ""), (r.get("notime") or "")[:10]
        if when < cutoff:
            continue
        if any(k in t for k in _RISK_KW):
            out["risk"].append(t)
        elif any(k in t for k in _GOOD_KW):
            out["good"].append(t)
    return out

def overnight_line():
    """隔夜外盘: 纳指/恒生(腾讯) + A50期货(新浪)。失败返回空串"""
    import re as _re
    parts = []
    try:
        raw = _get_gbk("https://qt.gtimg.cn/q=usIXIC,hkHSI")
        for m in _re.finditer(r'v_(\w+)="([^"]*)"', raw):
            f = m.group(2).split("~")
            if len(f) > 33 and f[32]:
                parts.append("%s%+.1f%%" % (f[1], float(f[32])))
    except Exception:
        pass
    try:
        req = urllib.request.Request(
            "https://hq.sinajs.cn/list=hf_CHA50CFD",
            headers={"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn"})
        with urllib.request.urlopen(req, timeout=10) as r:
            raw = r.read().decode("gbk", "ignore")
        d = raw.split('"')[1].split(",")
        cur, prev = float(d[0]), float(d[4])
        if prev > 0:
            parts.append("A50%+.2f%%" % ((cur / prev - 1) * 100))
    except Exception:
        pass
    if not parts:
        return ""
    s = "隔夜: " + " ".join(parts[:3])
    if any("纳指" in p and float(p.replace("%", "").replace("纳指", "").replace("+", "")) <= -2 for p in parts):
        s += " " + chr(92) + "u26a0" + chr(92) + "ufe0f纳指大跌，竞价注意低开风险"
    return s


def zt_pool(date=None):
    """涨停池原始列表（板块爆发扫描用）"""
    date = date or _latest_trading_date()
    return _pool("ZT", date) if date else []


def is_ebb():
    """退潮期判定(买入闸门): 炸板率>=25 或 高度板较前日降>=2 或 跌停>=10。
    返回 (bool, reason)"""
    try:
        em = emotion()
        if "error" in em:
            return False, ""
        # 开盘首小时池数据是小样本噪音(9:45炸板率27.5%的教训): 用昨日完整数据判定
        if _dt.datetime.now().time() < _dt.time(10, 0) and em["date"] == _dt.datetime.now().strftime("%Y%m%d"):
            prev_d = prev_trading_date(em["date"])
            if prev_d:
                em = emotion(prev_d)
        reasons = []
        if em["zb_rate"] >= 25:
            reasons.append("炸板率%.1f%%" % em["zb_rate"])
        prev_d = prev_trading_date(em["date"])
        if prev_d:
            pm = emotion(prev_d).get("max_lb", 0)
            if pm and em["max_lb"] <= pm - 2:
                reasons.append("高度板%d→%d" % (pm, em["max_lb"]))
        if em["dt"] >= 10:
            reasons.append("跌停%d家" % em["dt"])
        return (bool(reasons), "; ".join(reasons))
    except Exception:
        return False, ""


def sector_dist(date=None, top=8):
    """涨停池行业分布(当前时刻)"""
    from collections import Counter
    date = date or _latest_trading_date()
    if not date:
        return []
    heat = Counter(p.get("hybk", "其他") for p in _pool("ZT", date))
    return heat.most_common(top)
