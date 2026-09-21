# -*- coding: utf-8 -*-
"""财经快讯（财联社电报同级替代源：东财7x24快讯为主 + 新浪7x24兜底，免key）
财联社官方接口有签名反爬，此二源内容与其基本同源同步。全部尽力而为：失败返回空。
"""
import json, time, urllib.request

_UA = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
       "Referer": "https://kuaixun.eastmoney.com/"}

def _get_json(url, timeout=10):
    req = urllib.request.Request(url, headers=_UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8", "ignore"))

def fetch_news(limit=40):
    """返回 [{time, text}]，time为 'YYYY-MM-DD HH:MM' 或短时间"""
    out = []
    try:  # 东财7x24
        url = ("https://np-listapi.eastmoney.com/comm/web/getFastNewsList?client=web&biz=web_724"
               "&fastColumn=102&sortEnd=&pageSize=%d&req_trace=%d" % (limit, int(time.time() * 1000)))
        rows = (_get_json(url).get("data") or {}).get("fastNewsList") or []
        for it in rows:
            out.append({"time": (it.get("showTime") or "")[:16], "text": it.get("summary") or ""})
    except Exception:
        pass
    if not out:
        try:  # 新浪7x24兜底
            url = ("https://zhibo.sina.com.cn/api/zhibo/feed?page=1&page_size=%d&zhibo_id=152&tag_id=0" % limit)
            rows = ((_get_json(url).get("result") or {}).get("data") or {}).get("feed", {}).get("list") or []
            for it in rows:
                t = time.strftime("%m-%d %H:%M", time.localtime(int(it.get("create_time") or 0)))
                out.append({"time": t, "text": (it.get("rich_text") or "").strip()})
        except Exception:
            pass
    return out

_WATCH_KWS = ("医药", "医疗", "制药", "药", "地产", "房", "半导体", "芯片", "算力", "AI",
              "央行", "证监会", "国务院", "政策", "关税", "集采", "降准", "利率", "重组", "并购")

def filter_news(names, extra_kws=(), top=5, hours=None):
    """按 标的名+板块/宏观关键词 过滤；hours=None取全部。返回前top条"""
    kws = tuple(names) + tuple(extra_kws) + _WATCH_KWS
    hits, seen = [], set()
    now_ts = time.time()
    for it in fetch_news():
        if hours and it["time"] and "-" in it["time"]:
            try:
                ts = time.mktime(time.strptime("2026-" + it["time"], "%Y-%m-%d %H:%M"))
                if now_ts - ts > hours * 3600:
                    continue
            except Exception:
                pass
        text = it["text"]
        if any(k in text for k in kws if k) and text[:20] not in seen:
            seen.add(text[:20])
            hits.append(it)
    return hits[:top]

def news_lines(names, top=5, hours=None, label="快讯要点"):
    rows = filter_news(names, top=top, hours=hours)
    if not rows:
        return []
    return [label + ":"] + ["- %s %s" % (r["time"][-5:], r["text"].replace(chr(10), " ")[:70]) for r in rows]
