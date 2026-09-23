"""Read-only public market-data provider for the personal assistant."""
import copy
import datetime as dt
import json
import math
import re
import threading
import time
import urllib.parse
import urllib.request


_QUOTE_URL = "https://qt.gtimg.cn/q="
_KLINE_URL = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param={code},day,,,60,qfq"
_FINANCE_URL = "https://datacenter.eastmoney.com/securities/api/data/v1/get"
_INDEXES = (("sh000001", "上证指数"), ("sz399001", "深证成指"), ("sz399006", "创业板指"))
_CODE = re.compile(r"^(?:sh|sz)\d{6}$")
_CACHE = {}
_LOCK = threading.RLock()


def _now():
    return dt.datetime.now().replace(microsecond=0).isoformat()


def _normalize_code(code):
    value = str(code).lower()
    if not _CODE.fullmatch(value):
        raise ValueError("code must be sh/sz followed by six digits")
    return value


def _fetch_bytes(url, timeout=8):
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _fetch_json(url, timeout=8):
    return json.loads(_fetch_bytes(url, timeout).decode("utf-8", "ignore"))


def _number(value):
    try:
        number = float(value)
        return number if math.isfinite(number) else None
    except (TypeError, ValueError):
        return None


def _parse_tencent_quotes(raw):
    output = {}
    for match in re.finditer(r'v_(\w+)="([^"]*)"', raw.decode("gbk", "ignore")):
        code, fields = match.group(1).lower(), match.group(2).split("~")
        if len(fields) < 38 or not fields[3]:
            continue
        price, pct = _number(fields[3]), _number(fields[32])
        if price is None or pct is None:
            continue
        volume, amount = _number(fields[36]), _number(fields[37])
        vwap = round(amount * 100 / volume, 3) if volume and amount is not None else None
        output[code] = {
            "code": code, "name": fields[1], "price": price, "pct": pct,
            "prev_close": _number(fields[4]), "open": _number(fields[5]),
            "high": _number(fields[33]), "low": _number(fields[34]),
            "volume": volume, "amount": amount, "vwap": vwap,
            "limit_up": _number(fields[47]) if len(fields) > 47 else None,
            "limit_down": _number(fields[48]) if len(fields) > 48 else None,
            "time": fields[30],
        }
    return output


def _parse_candles(payload, code):
    series = ((payload.get("data") or {}).get(code) or {})
    rows = series.get("qfqday") or series.get("day") or []
    candles = []
    for row in rows:
        if not isinstance(row, list) or len(row) < 6:
            continue
        values = [_number(value) for value in row[1:6]]
        if any(value is None for value in values):
            continue
        candles.append({"date": str(row[0])[:10], "open": values[0], "close": values[1],
                        "high": values[2], "low": values[3], "volume": values[4]})
    return candles


def _finance_rows(payload):
    return ((payload.get("result") or {}).get("data") or []) if isinstance(payload, dict) else []


def _parse_financials(income, cash):
    cash_by_date = {str(row.get("REPORT_DATE") or "")[:10]: _number(row.get("NETCASH_OPERATE"))
                    for row in _finance_rows(cash)}
    records = []
    for row in _finance_rows(income):
        date = str(row.get("REPORT_DATE") or "")[:10]
        if not date:
            continue
        records.append({
            "date": date, "revenue": _number(row.get("TOTAL_OPERATE_INCOME")),
            "net_profit": _number(row.get("PARENT_NETPROFIT")),
            "revenue_growth": _number(row.get("TOTAL_OPERATE_INCOME_YOY")),
            "net_profit_growth": _number(row.get("PARENT_NETPROFIT_YOY")),
            "cash_flow": cash_by_date.get(date),
        })
    return records


def _get_cached(key, ttl):
    with _LOCK:
        entry = _CACHE.get(key)
        if entry and time.monotonic() - entry[0] < ttl:
            return copy.deepcopy(entry[1])
    return None


def _store_cached(key, value):
    with _LOCK:
        _CACHE[key] = (time.monotonic(), copy.deepcopy(value))
    return value


def _clear_cache():
    """Test-only cache reset."""
    with _LOCK:
        _CACHE.clear()


def get_market():
    """Return public index quotes. Provider failure is represented by a warning."""
    cached = _get_cached("market", 30)
    if cached is not None:
        return cached
    codes = [code for code, _ in _INDEXES]
    try:
        quotes = _parse_tencent_quotes(_fetch_bytes(_QUOTE_URL + ",".join(codes)))
        indices = []
        for code, fallback_name in _INDEXES:
            if code in quotes:
                row = dict(quotes[code])
                row["name"] = row["name"] or fallback_name
                indices.append(row)
        warnings = [] if len(indices) == len(codes) else ["腾讯行情数据不完整"]
    except Exception:
        indices, warnings = [], ["腾讯行情不可用"]
    return _store_cached("market", {"indices": indices, "as_of": _now(), "warnings": warnings})


def get_quote(code):
    """Return a fresh single Tencent quote without loading candles or financials."""
    code = _normalize_code(code)
    key = "quote:" + code
    cached = _get_cached(key, 30)
    if cached is not None:
        return cached
    try:
        quote = _parse_tencent_quotes(_fetch_bytes(_QUOTE_URL + code)).get(code) or {}
        warnings = [] if quote else ["腾讯个股行情不可用"]
    except Exception:
        quote, warnings = {}, ["腾讯个股行情不可用"]
    return _store_cached(key, {"code": code, "quote": quote, "warnings": warnings, "as_of": _now()})


def _finance_url(report, code):
    secu = code[2:] + (".SH" if code.startswith("sh") else ".SZ")
    query = urllib.parse.urlencode({
        "reportName": report, "columns": "ALL", "filter": '(SECUCODE="%s")' % secu,
        "pageNumber": 1, "pageSize": 8, "sortTypes": -1, "sortColumns": "REPORT_DATE",
        "source": "WEB", "client": "WEB",
    })
    return _FINANCE_URL + "?" + query


def get_stock(code):
    """Return quote, recent adjusted daily candles, and disclosed financial panels."""
    code = _normalize_code(code)
    key = "stock-panels:" + code
    cached = _get_cached(key, 300)
    quote_result = get_quote(code)
    warnings = list(quote_result["warnings"])
    if cached is not None:
        return {
            **cached, "quote": quote_result["quote"], "warnings": warnings + cached["warnings"],
            "as_of": quote_result["as_of"],
        }
    sources = [
        {"name": "腾讯行情与日线", "url": _QUOTE_URL + code},
    ]
    candles, financials = [], []
    try:
        candles = _parse_candles(_fetch_json(_KLINE_URL.format(code=code)), code)
    except Exception:
        warnings.append("腾讯日线不可用")
    if code not in {item[0] for item in _INDEXES}:
        sources += [
            {"name": "东方财富利润表", "url": _finance_url("RPT_F10_FINANCE_GINCOME", code)},
            {"name": "东方财富现金流量表", "url": _finance_url("RPT_F10_FINANCE_GCASHFLOW", code)},
        ]
        income, cash = {}, {}
        try:
            income = _fetch_json(_finance_url("RPT_F10_FINANCE_GINCOME", code))
        except Exception:
            warnings.append("东方财富利润表不可用")
        try:
            cash = _fetch_json(_finance_url("RPT_F10_FINANCE_GCASHFLOW", code))
        except Exception:
            warnings.append("东方财富现金流量表不可用")
        financials = _parse_financials(income, cash)
        if not financials and not warnings:
            warnings.append("东方财富财务数据为空")
    panels = {
        "code": code, "candles": candles, "financials": financials,
        "warnings": [item for item in warnings if item not in quote_result["warnings"]],
        "sources": sources,
    }
    _store_cached(key, panels)
    return {
        **panels, "quote": quote_result["quote"], "warnings": warnings,
        "as_of": quote_result["as_of"],
    }
